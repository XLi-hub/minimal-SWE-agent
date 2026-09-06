# 工具参考

内置工具 schema 的权威来源是
[`tooling/schemas.py`](../../src/mini_agent/tooling/schemas.py)，handler 与 registry 位于
[`tools.py`](../../src/mini_agent/tools.py)。YAML 的 `tools.enabled` 决定可见与可执行集合。

## bash

```text
bash(command: string, lines?: integer, timeout?: integer)
```

- `command` 必填，在当前 Environment 中执行；
- `lines` 覆盖本次 observation 的最大行数；
- `timeout` 覆盖本次等待秒数；
- 返回格式包含 output、return code、execution exception；
- 输出同时受配置字符预算限制。

正常非零退出保留真实 return code。timeout 或执行机械错误通常返回 `returncode=-1` 和
`exception_info`。SWE-bench 离线 profile 会拒绝明显网络命令，并把 schema 描述改成离线。

## read

```text
read(path: string, line_start?: integer >= 1, lines?: integer >= 1)
```

读取 Environment 中的 UTF-8 文件并添加行号。`line_start` 为 1-based，默认从第一行开始；
按提示继续下一块，避免反复读取整个大文件。文件不存在、目录路径和 I/O 错误变成 observation。

成功与错误文本都受字符预算限制。

## edit

```text
edit(path: string, old_string: string, new_string: string)
```

读取文件，要求 `old_string` 精确出现一次，然后通过 Environment 写回。零次匹配和多次匹配
都失败；应加入足够上下文使目标唯一。空 `new_string` 表示删除。该工具不做模糊匹配、patch
解析或自动格式化。

## write

```text
write(path: string, content: string)
```

创建或覆盖完整文件，父目录由具体 Environment 保证。它不是 append，也不会检查文件是否
已存在；覆盖前需要模型或调用者先 read。

## submit

```text
submit(output: string)
```

表达任务完成。普通任务可提交答案、patch 或摘要；SWE-bench prompt 要求完整 unified diff。
若启用 review，第一次 submit 只是 draft，第二次才终止。

同一 assistant 批次中 submit 后的工具会收到 skipped observation，不会执行副作用。

## trajectory

```text
trajectory(
  query?: string,
  start?: integer >= 0,
  events?: integer 1..50,
  event_type?: string,
  role?: string,
  tool_name?: string,
  returncode?: integer
)
```

只读检索当前运行的 append-only events：

- `query` 在序列化事件中做大小写不敏感搜索；
- `start` 是首个考虑的 sequence；
- `events` 限制匹配条目数；
- `event_type`、`role`、`tool_name`、`returncode` 是精确筛选；
- 多个条件按 AND 组合；
- tool result 可通过 call id 关联回 `tool_name`。

返回条数和字符数都有界，包含继续分页的 start 提示。该工具默认只在 SWE-bench review
profile 启用。它会暴露原始事件，其中的 assistant 推理仍是不可信声明。

## 通用错误语义

dispatcher 在以下情况仍追加匹配 call id 的 `role=tool` error：

- 工具未注册；
- 工具已注册但当前 profile 禁用；
- `function.arguments` 不是合法 JSON；
- JSON 顶层不是 object；
- 必填参数缺失或类型/范围无效；
- handler 抛出异常。

模型看见错误后可以在下一轮修正。协议完整性不代表操作成功，消费者必须检查 observation。

## 输出截断

bash 和 read 先按行预算选取，再应用字符预算；超长单行无法绕过保护。截断提示说明遗漏量
和下一步读取方式。trajectory 也应用字符预算。edit/write 的成功确认保持简短，但 handler
异常同样经过统一 tool result 通道。

## ToolContext 与 ToolResult

handler 接收 `ToolContext(environment, config, event_log)`：

- 环境工具使用 environment；
- handler 从 config 读取默认预算和策略；
- 只有 trajectory 使用 event_log，普通环境工具不依赖它。

handler 返回 `ToolResult(content, submission=None)`。只有 submit 设置 `submission`；是否立即
退出由 dispatcher 与 Agent 的 review 状态决定。

## 扩展检查表

1. schema name 与 registry key 完全一致；
2. handler 不直接绕开 Environment 做文件 I/O；
3. 配置名单同时限制 schema 和执行；
4. 所有参数错误都有同 call id observation；
5. 大输出有明确预算；
6. 副作用工具在 submit/limit 后不会运行；
7. 新增 focused tests，并更新本页。

设计说明见[工具系统](../architecture/tool-system.md)。
