# Agent 循环

[`agent.py`](../../src/mini_agent/agent.py) 是系统的控制平面。它不负责 shell、文件编辑或
HTTP 细节，只负责在预算内维护消息协议、查询模型、分发工具并形成可审计结果。

## 构造与运行状态

`Agent(model, environment, config)` 接收外部依赖。Model 只需满足 `.query()` 调用形状，
Environment 则实现统一接口。构造阶段读取 context 参数；`run()` 每次重置以下状态：

- `messages`：当前模型将看到的 context view；
- `events`：从本次运行开始追加的完整事件；
- `_steps`：主循环模型决策轮数；
- `n_calls`：主查询加摘要查询的 API 调用数；
- `cost`：依据 provider usage 累积的估算费用；
- `exit_status`、`submission`、`error`：终局数据。

`run()` 先写 system 与 task 消息，再循环调用 `step()`。`AgentExit` 子类表达预期终止；
未知异常转成结构化 error。只要给出 output 路径，`finally` 都会尝试保存轨迹。

## 一轮 step

```text
_check_limits()
      │
      ├─► _maybe_compress() ──可能调用一次无工具摘要
      │          └─► 再检查时间/成本
      ▼
query() ──► model.query(messages, tools)
      │     记录 usage、assistant message
      ├─► 再检查时间/成本
      └─► execute_actions(message)
```

步数只在新决策轮开始时检查；刚完成的模型查询仍属于当前合法步骤。时间或成本可能在
查询返回时越界，所以执行任何副作用工具前会再次检查。若此时停止，本轮 assistant 已经
声明的每个 tool call 仍会得到 skipped observation，以保持 provider 协议完整。

## 查询与无工具响应

每次 `query()`：

1. `_steps` 与 `n_calls` 增一；
2. 将启用工具的 schemas 传给 Model；
3. 用响应中的 usage 计算成本；
4. 把 assistant 消息正规化后追加到 `messages`；
5. 若没有 tool calls，按 `no_tool_call_retries` 决定纠正或以 `no_tool_calls` 结束。

摘要请求不消耗 `_steps`，但消耗 `n_calls`、费用和墙钟时间。provider 已返回 usage、正文却
无法解析时，记账回调仍先执行，避免漏记已发生的请求。

## 工具批次与协议不变量

一个 assistant 响应可以包含多个 tool calls。`execute_actions()` 按顺序处理，并维护：

```text
assistant(tool_call A, tool_call B, submit C, tool_call D)
tool(A result)
tool(B result)
tool(C Submitted/Draft)
tool(D Skipped)
```

每个 `tool_call_id` 必须恰好对应一条 `role=tool` 消息。未知工具、禁用工具、畸形 JSON、
handler 异常和主动跳过都转成 observation，而不是留下孤儿调用。

## submit 与 review

普通配置中，合法 `submit(output=...)` 追加确认 observation，然后抛 `Submitted`，在完整
批次得到确认后退出。submit 后的工具不会执行副作用。

若配置 `submission_review_prompt`，第一次 submit 只是 draft：

1. draft 被确认，但运行不退出；
2. review prompt 作为新的 user 消息加入；
3. 可选 `submission_review_reset_context` 回到原始 system + task，降低作者上下文锚定；
4. 可选 checkpoint 从事件中提取命令、return code、文件路径与边界，并生成明确标为
   `untrusted_author_working_memory` 的摘要；
5. reviewer 可用只读 `trajectory` 工具回查；
6. 第二次 submit 才成为最终结果。

review 不是第二个独立 Agent，也没有隐藏 evaluator 反馈。它只是同一运行中的提交门；
是否启用、是否重置 context、是否建立 checkpoint 都由 profile 配置决定。checkpoint 开关
必须同时配合 review prompt 与 reset，否则 pydantic 校验直接拒绝。

## 退出状态

| 状态 | 含义 |
|---|---|
| `submitted` | 接受最终 submit |
| `no_tool_calls` | 无工具响应且纠正次数耗尽 |
| `max_steps` | 下一轮开始前已达到决策轮限制 |
| `max_time` | 墙钟预算耗尽 |
| `cost_limit` | 已配置且可计算的费用预算耗尽 |
| `interrupted` | 捕获 `KeyboardInterrupt` |
| `error` | 非预期异常 |

CLI 再把这些字符串映射为明确的进程退出码。`Agent.run()` 作为库接口则返回 dict，便于测试
或 runner 消费。

## 不变量清单

- 运行限制命中后不再派发副作用工具；
- assistant/tool round-trip 始终可交给 OpenAI-compatible provider 回放；
- 模型 context 可压缩，但原始事件不因压缩而覆盖；
- summary 请求与主请求使用同一成本口径；
- draft review 的候选 patch 不被当作可信证据；
- 保存失败以外的运行结果不会因正常退出路径而丢失。

相关页面：[工具系统](tool-system.md)、[上下文与记录](context-and-records.md)、
[轨迹格式](../reference/trajectory-format.md)。
