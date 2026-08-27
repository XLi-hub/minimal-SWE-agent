# 上下文压缩（Context Compression）

## 为什么需要

Agent 循环的每一步都会往 `messages` 里追加两条消息：模型思考 `assistant(tool_calls)`
和对应的 `tool` 结果。没有任何机制阻止这段历史增长：

- 默认 `max_steps=250`，每步 bash 输出同时限制为 100 行和 20,000 字符，避免普通多行日志及超长单行内容无限增长。
- `deepseek-v4-flash` 当前支持最高 1M token 上下文；模型服务端的窗口和 Agent 的压缩预算
  是两层设置。为控制长任务的内存与摘要开销，本项目建议把 `agent.context_window` 保守地
  设为 128K（例如 `-c agent.context_window=128000`），而不是默认使用整个服务端窗口。

超限的后果有两种：请求直接 HTTP 400，或模型**静默遗忘**早期上下文（原始任务、
早期决策、已改过的文件）——后者更隐蔽，agent 会开始重复做已经做过的事。

**解决思路**：当历史逼近上限时，用一次 LLM 调用把旧的中间对话折叠成一条结构化摘要，
只保留 system prompt + 原始任务 + 最近 N 轮 verbatim。这是**会话内**压缩——不写任何
持久化记忆文件，压缩结果只活在本次 `messages` 里。

## 两条设计决策

### 1. 用 `len(text) // 4` 估算 token，而不是引入 tiktoken

精确计数需要 tokenizer，而 DeepSeek 的 tokenizer 不在 `tiktoken` 的公开表里，为此引
一个依赖 + 一份词汇表文件，对"是否逼近上限"的阈值判断来说得不偿失。

`// 4` 是对中英文混合文本一个足够好的近似（中文约 1 字符/token，英文约 4 字符/token，
取 4 偏保守）。它只用于**触发**，不用于精确记账——成本统计仍用 API 返回的真实
`response.usage`。所以估算误差只会让压缩触发得早一点或晚一点，不会算错钱。

```python
def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)
```

`count_tokens` 在此基础上再加每条约 4 token 的结构框架开销，以及工具 schema 的
体积（工具定义也占上下文，逼近上限时必须计入）。

### 2. 压缩按「round-trip」原子单元切，绝不拆 tool-call 对

OpenAI/DeepSeek 协议要求：每条 `assistant` 消息里的 `tool_call_id` 必须被**紧随其后**
的 `tool` 消息一一回应，否则请求返回 HTTP 400。这意味着不能简单地"按 token 数截到
某一行"——一旦截断点落在 `assistant(tool_calls)` 和它的 `tool` 结果之间，下一轮请求
就会失败。

`group_round_trips` 把历史拆成原子单元：

```
[assistant(tool_calls)]   ┐ 一个单元，整体保留
[tool result]             ┘ 或整体丢弃
[assistant]               ── 无 tool_calls，单独成单元
[user "Error: ..."]       ── 单独成单元
```

压缩只对单元做取舍，从不在单元**内部**切开。

## 压缩策略

`compress()` 的核心是「夹头留尾」：

```
[system, user(task)]  +  摘要  +  最近 N 个单元 verbatim
  ↑ 永不动                ↑ 折叠        ↑ 保留最近上下文
```

- **头**：`messages[0]`（system prompt）和 `messages[1]`（原始任务）永不参与压缩——
  丢了任务 agent 就不知道在干嘛了。
- **中间**：其余单元序列化成可读文本，交给 summarizer。
- **尾**：最近 `keep_last_n_turns`（默认 4）个 round-trip 单元原样保留，保证模型对
  "刚才发生了什么"有完整记忆。

### 折叠式增量（fold-in）

压缩可能发生多次。第二次压缩时，中间已经有一条旧的摘要消息。`compress()` 会：

1. 识别带 `summary_marker`（默认 `[CONTEXT SUMMARY]`）前缀的 user 消息，剥离 marker 后当作 `existing_summary`。
2. 把旧摘要**排除**出待摘要内容（避免"把摘要再摘要进自己"）。
3. 调用 summarizer 时传入 `existing_summary + new_lines`，生成一条**新**摘要。

这样摘要内容随会话演进逐步累积，而不会嵌套出多层摘要。

### 触发时机

每次查询前检查：

```python
should_compress(messages, tools, context_window, threshold, reserve)
# = count_tokens(messages, tools) >= threshold * (context_window - reserve)
```

`reserve`（默认 2000）为下一轮模型的**输出**预留余量——压缩后的历史 + 输出 token
必须仍装得下窗口。触发条件、窗口大小、保留轮数都可在构造 `Agent` 时覆盖，测试用
极小 `context_window` 就能强制触发。

## 失败兜底

`compress()` 里唯一的网络调用是 `summarize()`（一次无工具的 `model.query`）。这段代码
包在**独立**的 `try/except` 里，和主循环的异常处理分开：

```python
if should_compress(...):
    try:
        compressed, summary_response = compress(
            messages,
            self.model,
            self.keep_last_n_turns,
            on_response=self._account_summary_response,
        )
        if summary_response is not None:            # 摘要也是一次真实 API 调用
            self._record_event(                      # 原始事件只追加，不覆盖
                "context_compression", summary_message=...
            )
        messages[:] = compressed                    # 只替换模型 context view
    except Exception:
        pass  # 摘要失败 → 保留完整历史
    self._check_tool_limits()  # 摘要可能已耗尽时间或费用，不能直接发下一次请求
```

`summarize()` 在 provider response 返回后、解析正文前调用记账回调，再连同完整 response
一起返回。因此即使 response 正文 malformed，只要其中有 usage，摘要调用仍会计入
`n_calls`（`api_calls`）和 `cost`（`instance_cost`）——统计与主循环查询一视同仁。
没有发生摘要时（无中间内容）`summary_response` 为 `None`，跳过记账。

摘要失败不会注入 `"Error: ..."` 消息（那会让 agent 误以为命令执行出错了），也不会
中断主循环——下一轮会再次尝试。代价是这一轮继续用超限的完整历史（可能被 API 拒绝），
但至少不因为"压缩"这个本可忽略的辅助功能把整个任务搞挂。

### 为什么用 `messages[:] = ...` 而不是 `messages = ...`

`messages`、`self.messages`、`result["messages"]` 是**同一个模型 context list 对象**的三个引用。
`compress()` 读原列表、返回新列表、不改原列表。如果写成 `messages = compress(...)`
（重新绑定），只有局部变量 `messages` 指向新列表，`self.messages` 和 `result["messages"]`
仍指向旧的、未压缩的列表——返回结果和实际继续对话的 context 就会分叉。

就地切片赋值 `messages[:] = compress(...)` 用新内容**原地替换**列表元素，三个 context
引用自然保持一致。独立的 append-only event journal 不参与切片替换，因此仍保留所有原始
assistant/tool 消息；持久化时写入 `.events.jsonl`，并在 `.traj.json` 中留下数量和路径索引。
每次成功压缩还会在事件中保存当时的精确 `context_messages` snapshot，因而既能审计原始事实，
也能还原下一次主循环查询实际看到的摘要视图。

## 已知边界（明确接受）

- **两个视图有意不同**：`.traj.json.messages` 是「摘要 + 尾部」的最终模型 context；
  `.events.jsonl` 是完整 verbatim 事件。复盘时不要把前者误称为完整轨迹。
- **模型默认不读取 event journal**：完整事件主要服务审计。若摘要质量不足，优先改进结构化
  摘要；只有确认需要时才考虑增加分页、限额、只读的历史检索工具。
- **参数错误也会保留协议完整性**：`execute_tool_call` 会捕获 `json.loads` 失败，以及
  参数解码后不是 JSON object 的情况，并为原来的 `tool_call_id` 追加一条 `tool` error
  observation。因此这类 malformed arguments 不会留下孤儿调用。若外部调用者事先手工
  构造了孤儿 `tool` 消息，`group_round_trips` 会把它作为独立单元处理；压缩不会再拆散
  已成对的 assistant/tool 消息。
- **summarizer 期间的 Ctrl+C**：`except Exception` 不捕获 `KeyboardInterrupt`，会传播到
  外层 `finally` 正常落盘。边缘情况。

## 参考项目怎么做

| 项目 | 做法 | 类型 |
|---|---|---|
| **SWE-agent** | `LastNObservations`：只保留最近 N 条观测，直接**截断/省略**旧内容 | 截断 |
| **OpenHands** | `LLMSummarizingCondenser`：token 占比触发，LLM 摘要 + 保留头尾 | LLM 摘要 |
| **Aider** | `ChatSummary`：对话摘要 + repo map，超出 token 时把旧对话换成摘要 | LLM 摘要 |
| **LangChain** | `ConversationSummaryBufferMemory`：滑动窗口 + 可选摘要缓冲 | 摘要 + 缓冲 |
| **MemGPT / Letta** | 把内存分层（核心/召回/工作记忆），自动换页到外存 | 记忆层级 |
| **Claude Code** | 自动 compact + 手动 `/compact`，摘要放进上下文继续 | LLM 摘要 |
| **ChatGPT / Cursor** | 后台"记忆"：从历史抽取事实存长期记忆，跨会话召回 | 跨会话记忆 |

共同点正是本项目采用的三条：**按 token 占比触发（~75–85%）**、**保留头尾只压中间**、
**结构化摘要**。区别在于持久化——MemGPT/Claude Code 有跨会话记忆，本项目用户明确选了
**仅会话内压缩**，所以不做持久化，保持最小。

## 对照代码

- 纯函数：[context.py](../src/mini_agent/context.py) — `estimate_tokens` / `count_tokens` /
  `should_compress` / `group_round_trips` / `flatten` / `summarize` / `compress`。
- 循环钩子：[agent.py](../src/mini_agent/agent.py) 的 `run()` 循环顶部。
- 常量（现为配置）：[config/default.yaml](../src/mini_agent/config/default.yaml) 的 `agent` 段 —
  `context_window` / `compress_threshold` / `reserve_tokens` / `keep_last_n_turns` /
  `summary_marker` / `summary_prompt`。
- 测试：[test_context.py](../tests/test_context.py)（纯函数 17 个）+ `test_agent.py`（Agent 级 6 个）。
