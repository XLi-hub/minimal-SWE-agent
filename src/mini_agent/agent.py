"""Agent 主循环 — 使用模型 tool calling 替代文本解析."""

import json
import time
from pathlib import Path

from src.mini_agent.config import Config, UNSET, get_default_config, render_template
from src.mini_agent.context import compress, should_compress
from src.mini_agent.cost import compute_cost
from src.mini_agent.tools import execute_tool_call, format_assistant_message


class Agent:
    """AI Agent：循环查询 → 工具调用 → 执行，直到完成用户任务。

    model 需提供 .query(messages, tools=None) → OpenAI response.
    environment 需提供 .execute(command, timeout=30) → str.

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
        self.cost = 0.0
        self.cost_limit: float | None = None
        self.exit_status = ""
        self.submission = ""

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

        tools = [self.config.tools.bash_tool, self.config.tools.submit_tool]

        self.messages = [
            {"role": "system", "content": agent_cfg.system_prompt},
            {"role": "user", "content": render_template(agent_cfg.instance_template, task=task)},
        ]
        self.n_calls = 0
        self.cost = 0.0
        self.cost_limit = cost_limit
        self.exit_status = "error"
        self.submission = ""

        messages = self.messages
        result: dict = {"exit_status": "error", "submission": "", "messages": messages}
        deadline = time.monotonic() + max_time if max_time is not None else None

        try:
            for _ in range(max_steps):
                if deadline is not None and time.monotonic() > deadline:
                    result["exit_status"] = "max_time"
                    return result
                if cost_limit is not None and cost_limit > 0 and self.cost >= cost_limit:
                    result["exit_status"] = "cost_limit"
                    return result
                if should_compress(
                    messages, tools,
                    self.context_window, self.compress_threshold,
                    self.reserve_tokens,
                ):
                    try:
                        compressed, summary_response = compress(
                            messages, self.model, self.keep_last_n_turns,
                            config=self.config,
                        )
                        # 摘要也是一次真实 API 调用 —— 计入调用次数与成本，
                        # 否则 model_stats 会漏算摘要那次的 token 用量。
                        if summary_response is not None:
                            self.n_calls += 1
                            self.cost += compute_cost(summary_response, self.config)
                        # 就地切片赋值：messages / self.messages / result["messages"]
                        # 是同一个 list 对象，切片赋值让三者保持一致（重绑定是隐蔽 bug）。
                        messages[:] = compressed
                    except Exception:
                        pass  # 摘要失败 → 跳过本轮压缩，继续用完整历史
                try:
                    self.n_calls += 1
                    response = self.model.query(messages, tools=tools)
                    self.cost += compute_cost(response, self.config)
                    choice = response.choices[0]
                    msg = choice.message

                    # No tool calls → treat as exit (legacy / fallback)
                    if not msg.tool_calls:
                        messages.append(
                            {"role": "assistant", "content": msg.content}
                        )
                        print("LM output:", msg.content)
                        result["exit_status"] = "no_tool_calls"
                        return result

                    print("LM output:", msg.content)

                    messages.append(format_assistant_message(msg))

                    for tc in msg.tool_calls:
                        if execute_tool_call(
                            tc, messages, result, self.environment, config=self.config
                        ):
                            return result

                except KeyboardInterrupt:
                    result["exit_status"] = "interrupted"
                    return result
                except Exception as e:
                    messages.append(
                        {"role": "user", "content": f"Error: {e}"}
                    )

            # Ran out of steps — return partial progress.
            result["exit_status"] = "max_steps"
            return result
        finally:
            # Sync the run outcome onto the agent so a later serialize()
            # call reflects it, then save the trajectory if requested.
            self.exit_status = result["exit_status"]
            self.submission = result["submission"]
            if output is not None:
                self.save(output)

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
                    "agent_type": (
                        f"{self.__class__.__module__}."
                        f"{self.__class__.__name__}"
                    ),
                },
                "exit_status": self.exit_status,
                "submission": self.submission,
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
        from src.mini_agent.model import Model            # noqa: E402
        from src.mini_agent.environments.local import LocalEnvironment  # noqa: E402
        _default_agent = Agent(Model(), LocalEnvironment())
    agent_cfg = get_default_config().agent
    return _default_agent.run(
        task,
        max_steps=agent_cfg.max_steps if max_steps is UNSET else max_steps,
        max_time=agent_cfg.max_time if max_time is UNSET else max_time,
        cost_limit=agent_cfg.cost_limit if cost_limit is UNSET else cost_limit,
    )
