# 工具系统

工具层把模型声明的 function call 转成受配置约束的运行时操作。公共入口仍在
[`tools.py`](../../src/mini_agent/tools.py)，可复用细节已经拆到 `tooling/`。

## 模块拆分

| 文件 | 责任 |
|---|---|
| `tools.py` | handlers、registry、启用选择、分发、tool message 追加 |
| `tooling/schemas.py` | OpenAI function schemas |
| `tooling/types.py` | `ToolDefinition`、`ToolContext`、`ToolResult` |
| `tooling/files.py` | 唯一替换与带行号读取格式化 |
| `tooling/output.py` | 执行结果格式、timeout 解码、行/字符双重截断 |
| `tooling/network.py` | 明显网络命令识别 |

这个拆分让 `tools.py` 保持“runtime orchestration”角色，并保留历史 import 路径；纯逻辑
可以脱离 Agent 和真实环境测试。

## 单一 registry

`TOOL_REGISTRY` 的每项是 `ToolDefinition(name, schema, handler)`。构造时校验 schema 中的
function name 与 registry 名称一致，也拒绝重复注册。

配置中的 `tools.enabled` 只存名称，但同时控制两件事：

1. `get_enabled_tool_schemas()` 只把启用 schema 发给模型；
2. `execute_tool_call()` 只允许执行同一名单中的 handler。

因此“模型看不到”和“运行时不授权”不会各维护一张容易漂移的表。未知配置名在准备
schemas 时失败；模型臆造的未知或禁用工具则得到结构化 error observation。

## 分发流程

```text
ToolCall(id, function.name, function.arguments)
  ├─ 查 registry
  ├─ 查 tools.enabled
  ├─ JSON decode，且顶层必须为 object
  ├─ 构造 ToolContext(environment, config, event_log)
  ├─ 调用 handler
  └─ append role=tool + 同一个 tool_call_id
```

handler 返回 `ToolResult(content, submission=None)`。submit 通过 `submission` 字段表达终局，
普通工具只返回 observation 文本。异常被捕获为错误 observation，让模型有机会修正参数或
换策略。

## 内置工具

- `bash`：调用 `Environment.execute()`，返回 output、return code 与 execution error；
- `read`：调用 `Environment.read_file()`，支持起始行和行数限制；
- `edit`：先读文件，要求 `old_string` 只出现一次，再写回；
- `write`：调用 `Environment.write_file()` 创建或覆盖文件；
- `submit`：提交答案或 patch；
- `trajectory`：只读查询当前 append-only event journal，普通 profile 默认不启用。

参数细节见[工具参考](../reference/tools.md)。

## 输出预算

模型输入必须同时防范两种大输出：很多行和单个超长行。bash/read 因此应用配置的
`default_max_lines` 与 `default_max_chars`。截断保留头尾和省略提示；错误文本和 timeout
提示也走字符上限，避免模型可控的路径或异常绕过预算。

`ExecutionResult` 的非零 return code 是正常命令结果，不等于执行器异常。环境启动失败、
timeout 等执行机械问题使用 `returncode=-1` 和 `exception_info`，最终 observation 明确显示
这三个维度。

## 文件工具语义

文件工具不直接使用宿主 `Path`，而是走 Environment：

```text
read ──► environment.read_file
edit ──► read_file ──► apply_edit ──► write_file
write ──► environment.write_file
```

这样 local 与 Docker 中的 `bash` 和文件工具都观察同一个工作树。`edit` 的唯一匹配约束
用于避免含糊替换；零处或多处匹配都返回错误，不猜测目标。

## 网络策略

当 `environment.block_network_commands=true` 时，bash handler 在执行前识别常见下载、
远程 Git 和包管理命令，并返回 policy error。同时发给模型的 bash 描述也改成离线语义。

这个识别器不是安全沙箱：间接命令、解释器动态代码或未覆盖程序仍可能发起网络访问。
SWE-bench 的实际网络边界由 Docker `--network=none` 提供；命令识别负责更早、更易懂的反馈。

## submit 的特殊路径

submit 是注册工具，但其效果由 Agent 控制：

- 普通模式：handler 返回 submission，dispatcher 追加确认并抛 `Submitted`；
- review 模式：dispatcher 只返回 draft 字符串，Agent 注入 review context；
- 同一批次 submit 之后的调用：追加 `Skipped`，不执行 handler；
- 查询后命中预算：所有已声明调用都追加 limit-specific `Skipped`。

这保证终止意图与工具协议同时成立。

## 扩展一个工具

1. 在 `tooling/schemas.py` 定义 schema；
2. 在 `tools.py` 写接收 `(args, ToolContext)` 的 handler；
3. 将二者组成 `ToolDefinition` 加入 registry；
4. 在目标 YAML profile 的 `tools.enabled` 中选择它；
5. 测试 schema/name 一致、参数错误、成功路径、权限与输出边界。

不要只把 schema 加入发送列表，也不要在 Agent 里添加按名称分支。registry 是唯一映射点。

相关页面：[Agent 循环](agent-loop.md)、[工具参考](../reference/tools.md)、
[工具调用演进](../decisions/tool-calling-evolution.md)。
