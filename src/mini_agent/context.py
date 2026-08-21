"""上下文压缩 —— 字符数估算 token，超阈值时调用 LLM 做增量摘要.

Agent 循环每步都会往 ``messages`` 里追加 assistant/tool 消息，历史会无限增长。
这里提供两个能力：

1. **token 估算**：用 ``len(text) // 4`` 估算 token 数（DeepSeek 的 tokenizer
   不在 tiktoken 里，近似值对"是否逼近上限"的阈值判断足够）。
2. **压缩**：当历史逼近上限时，把中间的旧对话折叠成一条结构化摘要，只保留
   system prompt、原始任务、以及最近 N 轮 verbatim。

关键约束：OpenAI/DeepSeek 要求每条 ``assistant`` 消息里的 ``tool_call_id`` 必须
被紧随其后的 ``tool`` 消息一一回应，否则 HTTP 400。因此压缩按
「``assistant(tool_calls)`` + 其全部 ``tool`` 结果」作为一个**原子单元**整体
保留或丢弃，绝不能从中间切。
"""

import json

from mini_agent.config import Config, UNSET, get_default_config, render_template


def estimate_tokens(text: str) -> int:
    """估算 token 数（约 4 字符/token）。近似值，足够用于阈值触发。"""
    return max(1, len(text) // 4)


def count_tokens(messages: list[dict], tools: list[dict] | None = None) -> int:
    """估算整条 prompt 的 token：每条消息内容 + 每条约 4 token 框架开销 + 工具 schema。"""
    total = 0
    for msg in messages:
        total += 4  # role/结构框架开销
        content = msg.get("content")
        if content is not None:  # assistant 只带 tool_calls 时 content 可为 None
            total += estimate_tokens(str(content))
        if msg.get("tool_calls"):
            total += estimate_tokens(json.dumps(msg["tool_calls"]))
    if tools:
        total += estimate_tokens(json.dumps(tools))
    return total


def should_compress(messages, tools, context_window, threshold, reserve) -> bool:
    """历史是否逼近上限：``count_tokens >= threshold * (context_window - reserve)``。"""
    return count_tokens(messages, tools) >= threshold * (context_window - reserve)


def group_round_trips(messages: list[dict]) -> list[list[dict]]:
    """拆成原子单元。

    带 ``tool_calls`` 的 ``assistant`` 消息与紧随其后的所有 ``tool`` 消息归入
    同一单元（绝不能拆开）；其余每条消息单独成单元。
    """
    units: list[list[dict]] = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            unit = [msg]
            i += 1
            while i < len(messages) and messages[i].get("role") == "tool":
                unit.append(messages[i])
                i += 1
            units.append(unit)
        else:
            units.append([msg])
            i += 1
    return units


def flatten(units: list[list[dict]]) -> str:
    """把单元序列化成给 summarizer 看的可读文本（含工具名/参数/结果）。"""
    lines: list[str] = []
    for unit in units:
        for msg in unit:
            role = msg["role"]
            content = msg.get("content") or ""
            if role == "assistant" and msg.get("tool_calls"):
                lines.append(f"[assistant] {content}".rstrip())
                for tc in msg["tool_calls"]:
                    fn = tc.get("function", {})
                    lines.append(
                        f"  tool_call {fn.get('name', '?')}({fn.get('arguments', '')})"
                    )
            elif role == "tool":
                lines.append(f"[tool result for {msg.get('tool_call_id', '?')}]\n{content}")
            else:
                lines.append(f"[{role}] {content}")
    return "\n".join(lines)


def summarize(model, existing_summary: str | None, new_lines: str,
              config: Config | None = None) -> tuple[str, object]:
    """一次无工具的 LLM 调用，把 ``new_lines`` 折叠进 ``existing_summary``。

    返回 ``(summary_text, response)`` —— 连同完整 response 一起返回，调用方才能
    从 ``response.usage`` 记账（否则摘要这次 API 调用会被漏算）。
    """
    prompt = render_template(
        (config or get_default_config()).agent.summary_prompt,
        existing_summary=existing_summary or "",
        new_lines=new_lines,
    )
    response = model.query([{"role": "user", "content": prompt}])
    return response.choices[0].message.content, response


def compress(messages, model, keep_last_n_turns=UNSET,
             config: Config | None = None) -> tuple[list[dict], object | None]:
    """压缩历史，返回 ``(新列表, 摘要响应)``：``[system, user(task)] + 摘要 + 最近 N 个单元``。

    只压缩「中间」——system prompt 和原始任务永不动，最近 ``keep_last_n_turns``
    个 round-trip 单元 verbatim 保留。若中间已有一条旧摘要消息，则把它的内容
    当作 ``existing_summary`` 折叠（增量），避免把摘要再摘要进自己。

    摘要响应在**没有发生摘要**时（无中间内容 / 中间只有旧摘要）为 ``None``，
    调用方据此判断是否要为这次摘要调用记账。
    """
    agent_cfg = (config or get_default_config()).agent
    if keep_last_n_turns is UNSET:
        keep_last_n_turns = agent_cfg.keep_last_n_turns
    marker = agent_cfg.summary_marker

    units = group_round_trips(messages)
    n = len(units)
    # system/task 永不当"尾部"；夹紧避免 keep=0 时 units[2:-0] 变成空切片，
    # 以及 keep >= len(units) 时重复 system/task。
    keep = max(0, min(keep_last_n_turns, n - 2))
    middle = units[2 : n - keep] if keep > 0 else units[2:]
    tail = units[n - keep :] if keep > 0 else []
    if not middle:
        return list(messages), None  # 无中间内容，不调 summarizer

    existing_summary: str | None = None
    to_summarize: list[list[dict]] = []
    for unit in middle:
        m = unit[0]
        if (
            len(unit) == 1
            and m.get("role") == "user"
            and isinstance(m.get("content"), str)
            and m["content"].startswith(marker)
        ):
            existing_summary = m["content"][len(marker) :].strip()
        else:
            to_summarize.append(unit)
    if not to_summarize:
        return list(messages), None  # 中间只有旧摘要，无新内容

    summary_text, response = summarize(
        model, existing_summary, flatten(to_summarize), config=config
    )
    summary_msg = {"role": "user", "content": f"{marker}\n{summary_text}"}
    return [messages[0], messages[1], summary_msg] + [
        m for unit in tail for m in unit
    ], response
