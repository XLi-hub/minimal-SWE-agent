"""Agent 主循环 — 使用模型 tool calling 替代文本解析."""

import json
import time
import traceback
from pathlib import Path

from mini_agent import __version__
from mini_agent.config import Config, UNSET, get_default_config, render_template
from mini_agent.context import compress, should_compress
from mini_agent.cost import compute_cost
from mini_agent.tools import (
    append_skipped_tool_result,
    execute_tool_call,
    format_assistant_message,
    get_enabled_tool_schemas,
)
from mini_agent.exceptions import (
    AgentExit,
    CostLimit,
    MaxSteps,
    MaxTime,
    NoToolCalls,
    Submitted,
)


class Agent:
    """AI Agent：循环查询 → 工具调用 → 执行，直到完成用户任务。

    model 需提供 .query(messages, tools=None) → OpenAI response.
    environment 需提供 .execute(command, timeout=30) → ExecutionResult.

    ``config`` 为可选的 :class:`Config`；缺省时用 ``default.yaml``。其余
    关键字参数用于按次覆盖个别配置项（``UNSET`` 哨兵表示「未指定，读配置」）。
    """

    def __init__(self, model, environment,
                 config: Config | None = None,
                 context_window=UNSET,
                 compress_threshold=UNSET,
                 reserve_tokens=UNSET,
                 keep_last_n_turns=UNSET):
        self.config = config or get_default_config()
        agent_cfg = self.config.agent
        self.model = model
        self.environment = environment
        self.context_window = (
            agent_cfg.context_window if context_window is UNSET else context_window
        )
        self.compress_threshold = (
            agent_cfg.compress_threshold if compress_threshold is UNSET else compress_threshold
        )
        self.reserve_tokens = (
            agent_cfg.reserve_tokens if reserve_tokens is UNSET else reserve_tokens
        )
        self.keep_last_n_turns = (
            agent_cfg.keep_last_n_turns if keep_last_n_turns is UNSET else keep_last_n_turns
        )
        # Trajectory state — reset and updated by run().
        self.messages: list[dict] = []
        self.n_calls = 0
        self._steps = 0
        self.cost = 0.0
        self.cost_limit: float | None = None
        self.exit_status = ""
        self.submission = ""
        self.error: dict | None = None
        self._consecutive_no_tool_calls = 0

    def run(self, task: str, max_steps=UNSET,
            max_time=UNSET,
            output: str | Path | None = None,
            cost_limit=UNSET) -> dict:
        """Run the agent loop for a given user task.

        Parameters
        ----------
        task:
            The user's task description.
        max_steps:
            Maximum tool-calling iterations before the agent stops
            (default: ``config.agent.max_steps`` = 250).  Each
            ``model.query()`` call counts as one step, regardless of how
            many tool calls the model makes in that step.
        max_time:
            Maximum wall-clock time in seconds for the entire run
            (default: ``config.agent.max_time`` = 1800).  Pass ``None`` to
            disable the time limit.
        output:
            Optional path (``.traj.json``) to save the trajectory to when
            the run finishes.  Pass ``None`` (default) to skip saving.
        cost_limit:
            Maximum accumulated cost in USD before the agent stops
            (default: ``config.agent.cost_limit`` = 3.0).  Pass ``0`` or
            ``None`` to disable the limit.

        Returns
        -------
        dict
            With keys:

            - ``exit_status``: one of ``"submitted"``, ``"no_tool_calls"``,
              ``"max_steps"``, ``"max_time"``, ``"cost_limit"``,
              ``"interrupted"``, ``"error"``
            - ``submission``: the final answer (empty if not submitted)
            - ``messages``: the full message history
        """
        agent_cfg = self.config.agent
        if max_steps is UNSET:
            max_steps = agent_cfg.max_steps
        if max_time is UNSET:
            max_time = agent_cfg.max_time
        if cost_limit is UNSET:
            cost_limit = agent_cfg.cost_limit

        # Per-run state read by the decomposed step/query/execute_actions methods.
        self._tools = get_enabled_tool_schemas(self.config)
        self._max_steps = max_steps
        self._deadline = time.monotonic() + max_time if max_time is not None else None
        self._steps = 0

        self.messages = [
            {"role": "system", "content": agent_cfg.system_prompt},
            {"role": "user", "content": render_template(agent_cfg.instance_template, task=task)},
        ]
        self.n_calls = 0
        self.cost = 0.0
        self.cost_limit = cost_limit
        self.exit_status = "error"
        self.submission = ""
        self.error = None
        self._consecutive_no_tool_calls = 0

        result: dict = {"exit_status": "error", "submission": "", "messages": self.messages}

        try:
            while True:
                try:
                    self.step()
                except AgentExit as e:
                    result["exit_status"] = e.exit_status
                    result["submission"] = e.submission
                    break
                except KeyboardInterrupt:
                    result["exit_status"] = "interrupted"
                    break
                except Exception as e:
                    self.messages.append({"role": "user", "content": f"Error: {e}"})
                    self.error = {
                        "type": type(e).__name__,
                        "message": str(e),
                        "traceback": traceback.format_exc(),
                    }
                    result["exit_status"] = "error"
                    result["error"] = self.error
                    break
        finally:
            # Sync the run outcome onto the agent so a later serialize()
            # call reflects it, then save the trajectory if requested.
            self.exit_status = result["exit_status"]
            self.submission = result["submission"]
            if output is not None:
                self.save(output)
        return result

    def step(self) -> None:
        """One iteration: enforce limits, compress if needed, query, execute tools."""
        self._check_limits()
        self._maybe_compress()
        message = self.query()
        if message is not None:
            # The model response itself may push us over a wall-clock or cost
            # budget.  Check again before dispatching any side-effecting tool,
            # but do not re-check max_steps here: this query already consumed
            # the current step and the loop boundary owns that limit.
            try:
                self._check_tool_limits()
            except AgentExit as exc:
                # Keep the trajectory valid for providers that require one
                # tool response per assistant tool_call, even though no
                # handler can safely run after a terminal budget is hit.
                for tc in message.tool_calls:
                    append_skipped_tool_result(
                        tc,
                        self.messages,
                        reason=f"run limit '{exc.exit_status}' was reached before execution",
                    )
                raise
            self.execute_actions(message)

    def _check_limits(self) -> None:
        """Raise the matching :class:`AgentExit` when a run limit has been hit.

        Order mirrors the original loop: the step budget (the old ``for``
        boundary) is checked first, then wall-clock time, then cost.
        """
        if self._steps >= self._max_steps:
            raise MaxSteps()
        if self._deadline is not None and time.monotonic() > self._deadline:
            raise MaxTime()
        if self.cost_limit is not None and self.cost_limit > 0 and self.cost >= self.cost_limit:
            raise CostLimit()

    def _check_tool_limits(self) -> None:
        """Check limits that must hold immediately before tool dispatch.

        ``query()`` updates ``cost`` after the request returns, and a model
        request can itself consume the remaining wall-clock budget.  Tool
        calls must not run after that point because they may mutate the
        workspace.  ``max_steps`` is intentionally excluded; it is checked
        only at the start of the next loop iteration.
        """
        if self._deadline is not None and time.monotonic() > self._deadline:
            raise MaxTime()
        if self.cost_limit is not None and self.cost_limit > 0 and self.cost >= self.cost_limit:
            raise CostLimit()

    def query(self):
        """Query the model once, append the assistant message, return the raw message.

        Returns ``None`` while a configured no-tool-call correction is pending.
        Raises :class:`NoToolCalls` after those retries are exhausted.
        """
        self._steps += 1
        self.n_calls += 1
        response = self.model.query(self.messages, tools=self._tools)
        self.cost += compute_cost(response, self.config)
        choice = response.choices[0]
        msg = choice.message

        # No tool calls → treat as exit (legacy / fallback)
        if not msg.tool_calls:
            self.messages.append({"role": "assistant", "content": msg.content})
            print("LM output:", msg.content)
            self._consecutive_no_tool_calls += 1
            if self._consecutive_no_tool_calls <= self.config.agent.no_tool_call_retries:
                self.messages.append({
                    "role": "user",
                    "content": (
                        "Your previous response did not call a tool. Continue the task "
                        "and finish with a valid tool call; use submit only when the "
                        "requested work is complete."
                    ),
                })
                return None
            raise NoToolCalls()

        self._consecutive_no_tool_calls = 0
        print("LM output:", msg.content)
        self.messages.append(format_assistant_message(msg))
        return msg

    def execute_actions(self, msg) -> None:
        """Execute every tool call in *msg*, appending tool results to messages.

        ``execute_tool_call`` raises :class:`Submitted` on ``submit``.  The
        rest of the assistant batch still receives deterministic tool results
        so every advertised ``tool_call_id`` is acknowledged, while the
        skipped handlers are never dispatched.
        """
        submission: Submitted | None = None
        for tc in msg.tool_calls:
            if submission is not None:
                append_skipped_tool_result(tc, self.messages)
                continue
            try:
                execute_tool_call(tc, self.messages, self.environment, config=self.config)
            except Submitted as exc:
                submission = exc

        if submission is not None:
            # Raise only after the full assistant batch has been acknowledged.
            raise Submitted(submission.submission)

    def _maybe_compress(self) -> None:
        """Summarize the middle of the history when it nears the context window.

        A compression failure is non-fatal: it is silently skipped and the loop
        continues with the full (uncompressed) history.
        """
        if not should_compress(
            self.messages, self._tools,
            self.context_window, self.compress_threshold, self.reserve_tokens,
        ):
            return
        try:
            compressed, summary_response = compress(
                self.messages, self.model, self.keep_last_n_turns, config=self.config,
            )
            # 摘要也是一次真实 API 调用 —— 计入调用次数与成本，
            # 否则 model_stats 会漏算摘要那次的 token 用量。
            if summary_response is not None:
                self.n_calls += 1
                self.cost += compute_cost(summary_response, self.config)
            # 就地切片赋值，保持 self.messages / result["messages"] 别名一致。
            self.messages[:] = compressed
        except Exception:
            pass  # 摘要失败 → 跳过本轮压缩，继续用完整历史

    def serialize(self) -> dict:
        """Serialize the agent trajectory to a JSON-compatible dict.

        Captures the full message history plus run metadata (exit status,
        submission, model call stats).  Use :meth:`save` to write it to
        disk as a ``.traj.json`` file.
        """
        return {
            "info": {
                "model_stats": {
                    "instance_cost": self.cost,
                    "api_calls": self.n_calls,
                },
                "config": {
                    "agent": self.config.agent.model_dump(mode="json"),
                    "agent_type": (
                        f"{self.__class__.__module__}."
                        f"{self.__class__.__name__}"
                    ),
                    "model": self.config.model.model_dump(mode="json"),
                    "model_type": (
                        f"{self.model.__class__.__module__}."
                        f"{self.model.__class__.__name__}"
                    ),
                    "environment": self.config.environment.model_dump(mode="json"),
                    "environment_type": (
                        f"{self.environment.__class__.__module__}."
                        f"{self.environment.__class__.__name__}"
                    ),
                },
                "mini_version": __version__,
                "exit_status": self.exit_status,
                "submission": self.submission,
                "error": self.error,
            },
            "messages": self.messages,
            "trajectory_format": "mini-agent-0.1",
        }

    def save(self, path: str | Path | None) -> dict:
        """Serialize the trajectory and write it to *path*.

        The path conventionally ends in ``.traj.json``.  Parent
        directories are created as needed.  When *path* is ``None`` no
        file is written (the serialized data is still returned).
        """
        data = self.serialize()
        if path is not None:
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, indent=2))
        return data


# 向后兼容：延迟创建，避免 import 时就需要 API key
_default_agent: Agent | None = None


def run(task: str, max_steps=UNSET,
        max_time=UNSET,
        cost_limit=UNSET) -> dict:
    """Convenience wrapper around the default ``Agent``.

    ``UNSET`` arguments fall back to the default config's ``agent`` values,
    so ``run("fix the bug")`` behaves exactly like the old module-level API.
    """
    global _default_agent
    if _default_agent is None:
        from mini_agent.model import Model            # noqa: E402
        from mini_agent.environments.local import LocalEnvironment  # noqa: E402
        _default_agent = Agent(Model(), LocalEnvironment())
    agent_cfg = get_default_config().agent
    return _default_agent.run(
        task,
        max_steps=agent_cfg.max_steps if max_steps is UNSET else max_steps,
        max_time=agent_cfg.max_time if max_time is UNSET else max_time,
        cost_limit=agent_cfg.cost_limit if cost_limit is UNSET else cost_limit,
    )
