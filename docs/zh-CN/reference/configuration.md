# 配置参考

本页按 pydantic 模型列出字段职责，不复制易漂移的默认数字。当前默认值请直接查看
[`default.yaml`](../../../src/mini_agent/config/default.yaml) 和
[`swebench.yaml`](../../../src/mini_agent/config/benchmarks/swebench.yaml)。类型与约束的权威来源是
[`config/models.py`](../../../src/mini_agent/config/models.py)。

## 顶层 Config

| section | 模型 | 消费者 |
|---|---|---|
| `model` | `ModelConfig` | Model、benchmark model factory |
| `agent` | `AgentConfig` | Agent、context、prompt rendering |
| `tools` | `ToolsConfig` | registry selection、handlers |
| `cost` | `CostConfig` | `compute_cost()` |
| `environment` | `EnvironmentConfig` | factory、local/Docker、network policy |
| `run` | `RunConfig` | benchmark runner |

所有模型 `extra="forbid"`：未知字段是配置错误，不会被静默忽略。
`get_default_config()` 会解析并缓存一个内部模板，但对调用者返回隔离的深拷贝，因此库调用者
修改配置不会污染后续 Agent 看到的默认值。

## model

| 字段 | 类型 | 含义 |
|---|---|---|
| `model_name` | `str` | 发给 provider 的模型标识，不能为空 |
| `base_url` | `str \| null` | OpenAI-compatible endpoint；null 使用 SDK 默认 |
| `api_key_env` | `str` | 保存密钥的环境变量名称，不能为空 |
| `model_kwargs` | object | 原样传给 Chat Completions create 的额外参数 |

`model_kwargs` 禁止覆盖 `model`、`messages`、`tools`，因为这些由调用点控制。真实 API key
不是配置字段。

## agent

| 字段 | 类型/约束 | 含义 |
|---|---|---|
| `system_prompt` | 必填 string | 首条 system message |
| `instance_template` | 必填 string | 用 `task` 渲染 user message |
| `summary_prompt` | 必填 string | 用 `existing_summary`、`new_lines` 渲染摘要请求 |
| `summary_marker` | string | 标识 messages 中的压缩摘要 |
| `context_window` | positive int | Agent 自己的估算窗口，不是 provider 声明 |
| `compress_threshold` | `(0, 1]` finite float | 可用窗口的压缩触发比例 |
| `reserve_tokens` | nonnegative int | 为下一次输出预留；必须小于 context window |
| `keep_last_n_turns` | nonnegative int | 压缩后原样保留的最近原子单元数 |
| `max_steps` | positive int | 主循环模型决策轮限制 |
| `max_time` | positive float 或 null | 总墙钟限制；null 关闭 |
| `cost_limit` | nonnegative float 或 null | 累计 USD 限制；0/null 关闭 |
| `no_tool_call_retries` | nonnegative int | 无 tool calls 后的纠正次数 |
| `submission_review_prompt` | nonblank string 或 null | 首次 submit 后的 review 指令 |
| `submission_review_reset_context` | bool | review 是否重置作者 messages |
| `submission_review_checkpoint_context` | bool | 是否携带 evidence + 不可信摘要 |

checkpoint 为 true 时，review prompt 必须存在且 reset 必须为 true。

## tools

| 字段 | 类型/约束 | 含义 |
|---|---|---|
| `enabled` | 非空、唯一 string list | 按顺序选择 schema 与执行权限 |
| `default_max_lines` | positive int | bash/read 默认行预算 |
| `default_max_chars` | positive int | 工具 observation 字符预算 |
| `default_timeout` | positive int | bash 默认 timeout |

启用名称还必须出现在 `TOOL_REGISTRY`；该检查发生在获取 schemas 时。

## cost

| 字段 | 单位 |
|---|---|
| `price_input_per_1m` | 每百万未缓存输入 token 的 USD |
| `price_input_cache_hit_per_1m` | 每百万缓存命中输入 token 的 USD |
| `price_output_per_1m` | 每百万输出 token 的 USD |

三者都是有限、非负浮点数。默认不假设 provider 价格；批量运行前自行核对当前报价。

## environment

| 字段 | 含义 |
|---|---|
| `type` | factory 名称，当前为 local/docker |
| `env` | 注入命令环境的字符串键值；轨迹中会脱敏其值 |
| `image` | Docker image |
| `cwd` | 容器命令和文件工具的工作目录 |
| `timeout` | Environment 默认命令 timeout |
| `container_timeout` | 长寿命容器保持命令的时长表达式 |
| `forward_env` | 从宿主转发到 Docker 的变量名；也是 Local 使用受保护变量的显式许可 |
| `protected_env` | Local 命令默认不继承的宿主变量名；始终包含 `model.api_key_env` |
| `executable` | Docker-compatible CLI 路径/命令 |
| `run_args` | 传给 container run 的额外参数 |
| `pull_timeout` | 拉取镜像 timeout |
| `interpreter` | 容器内执行命令的 argv，非空字符串 list |
| `block_network_commands` | 工具层是否拒绝明显网络命令 |

`block_network_commands` 不是沙箱；需要硬网络边界时使用容器网络配置。

Local 会继承普通宿主变量，让编译器等工具继续获得 `PATH` 等运行设置。provider 凭据例外：
配置的 `model.api_key_env` 会自动加入 `protected_env`。只有同时把名称显式加入
`forward_env`，命令才能获得受保护变量。轨迹的配置快照不会原样保存环境变量值。

## run

`env_startup_command` 是 optional Jinja2 string。benchmark 在环境创建后、Agent 启动前用
instance 字段渲染并执行；非零 structured return code 使 startup 失败并触发清理。

## 合并规则

`recursive_merge` 对嵌套 mapping 递归合并，后写值获胜；`UNSET` 表示该层未指定，不覆盖
下层。`None` 是合法值，会真实覆盖，例如关闭 `max_time`。

配置 spec 可以是 YAML 路径、内置名称或 dotted `key=value`。环境变量前缀默认
`MINI_AGENT_`，用 `__` 分隔层级。操作示例见[配置指南](../guides/configuration.md)。
