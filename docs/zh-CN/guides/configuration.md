# 配置指南

配置目标是让 prompt、预算、provider、工具和环境策略可组合，同时让 secrets 留在运行环境。
本页讲如何操作；字段定义见[配置参考](../reference/configuration.md)。

## 配置来源

最终 `Config` 由四层递归合并，后写优先：

```text
内置 default.yaml
  < --config 文件或 dotted key=value（从左到右）
  < MINI_AGENT_* 环境变量
  < 专用 CLI 参数
```

普通默认文件是
[`default.yaml`](../../../src/mini_agent/config/default.yaml)。SWE-bench CLI 会先额外叠加
[`benchmarks/swebench.yaml`](../../../src/mini_agent/config/benchmarks/swebench.yaml)。

## 使用 YAML 文件

只写想覆盖的嵌套字段：

```yaml
model:
  model_name: my-model
  base_url: https://provider.example/v1

agent:
  max_steps: 80

cost:
  price_input_per_1m: 1.0
  price_input_cache_hit_per_1m: 0.1
  price_output_per_1m: 4.0
```

运行：

```bash
minimal --config my-provider.yaml --task "检查项目"
```

`--config` 可重复，后面的文件或 key=value 覆盖前面，但不会抹掉未提及的同层字段。

## 覆盖单个字段

```bash
minimal -c agent.max_steps=50 -c environment.type=docker
minimal -c 'tools.enabled=["bash","submit"]'
minimal -c agent.cost_limit=null
```

值先尝试按 JSON 解析：数字、布尔、null、数组和对象保留类型；普通未加引号的文本作为字符串。
key 中不能出现空段。

环境变量用双下划线表达嵌套：

```bash
MINI_AGENT_AGENT__MAX_STEPS=50 minimal
MINI_AGENT_ENVIRONMENT__BLOCK_NETWORK_COMMANDS=true minimal
```

专用 CLI 参数如 `--max-steps`、`--env` 和 `--image` 优先级最高。未传入的 flag 使用内部
`UNSET` 哨兵，不能意外把 YAML 值覆盖成 `None`。

## 保护 API key

YAML 的 `model.api_key_env` 是环境变量名称，不是密钥：

```yaml
model:
  api_key_env: OPENAI_API_KEY
```

真实值放在未提交的 `.env` 或进程环境：

```bash
OPENAI_API_KEY=... minimal --task "检查项目"
```

不要把 key 放进 `model_kwargs`、trajectory、命令参数或可提交的 profile。配置的 provider key
变量会自动对 Local 命令隐藏。`forward_env` 应保持最小化：它把指定宿主变量转发进 Docker，
也会显式允许 Local 命令使用受保护变量。模型 provider key 通常只需留在宿主 Model。

## Prompt 模板

`agent.instance_template` 与 `summary_prompt` 使用 Jinja2 `{{ variable }}`。渲染启用
`StrictUndefined`，拼错变量会立即失败，不会静默生成缺字段 prompt。

自定义 system/instance prompt 时仍要保留：

- 允许的工作目录和安全策略；
- 工具使用与最终 submit 约定；
- 测试结果必须检查 return code；
- benchmark 中不得检索上游答案或修改测试的限制。

## 工具 profile

默认 profile 启用 bash、submit、read、edit、write。只用 bash 的教学对照可叠加：

```bash
minimal --config default_bash --task "检查项目"
```

`tools.enabled` 同时控制发给模型的 schema 和 dispatcher 权限。启用名称必须已在 registry
注册，列表不能为空、不能重复。

## Review 配置

设置 `submission_review_prompt` 后，首次 submit 成为 draft。若要 clean-context review：

```yaml
agent:
  submission_review_prompt: |
    Independently inspect the patch and tests, then submit again.
  submission_review_reset_context: true
  submission_review_checkpoint_context: true
```

checkpoint 依赖 prompt 和 reset，两者缺一时配置校验失败。它额外进行摘要请求，必须纳入
费用和时间估算。

## 验证配置

所有层合并后由 pydantic `Config` 校验：未知字段被拒绝，数值范围、工具名单、reserved
model kwargs 和 review 组合都有约束。修改内置 YAML 后运行：

```bash
conda run -n minimal-SWE-agent env PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python -m pytest tests/test_config.py tests/test_config_loading.py \
  tests/test_config_read_edit.py -q -p no:anyio
```

## 常见问题

- `KeyError: OPENAI_API_KEY`：设置 `api_key_env` 指向的环境变量；
- 配置看似没生效：检查是否被环境变量或专用 CLI flag 以更高优先级覆盖；
- 字符串被解析成数字/布尔：在 key=value 中使用 JSON 引号；
- cost limit 不停：为当前 provider 配置真实非零价格；
- Docker 选项影响 local：多数 Docker 字段在 local 中被忽略，这是共享 model 的设计结果。

返回[文档首页](../index.md)。
