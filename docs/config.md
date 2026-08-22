# 配置外置（YAML / 环境变量）

这是本项目从参考项目 [mini-swe-agent](https://github.com/swe-agent/mini-swe-agent) 移植的**最核心模式**。改造前，所有可调项都硬编码在旧 `config.py` 的模块级常量里——`SYSTEM_PROMPT`、`BASH_TOOL`、`DEFAULT_MAX_STEPS`、`PRICE_*`，甚至 `model.py` 里的 `deepseek-chat` / `base_url`。改任何一项都要动源码、重新跑测试。

改造后，全部搬进 [config/default.yaml](../src/mini_agent/config/default.yaml)，运行时由三样东西共同决定最终配置：

- **YAML** 存默认值、prompt 和启用的工具名单
- **环境变量** 覆盖单个字段（`MINI_AGENT_*`）
- **CLI 参数** 临时覆盖（`--config` / `-c`）

这套模式的两个教学重点是 `recursive_merge + UNSET` 和 **Jinja2 模板渲染**，下面分别讲。

---

## 1. 多来源优先级

最终配置是**四层 dict 递归合并**出来的，后写优先（低 → 高）：

```
内置 default.yaml  <  --config 文件/key=value（从左到右）  <  MINI_AGENT_* 环境变量  <  CLI 参数
```

对应 [config/__init__.py](../src/mini_agent/config/__init__.py) 里的 `build_config()`：

```python
layers = [load_default_yaml()]                    # 1. 权威默认值
for spec in config_specs or []:                   # 2. --config（可重复，后写优先）
    layers.append(get_config_from_spec(spec))
layers.append(_env_var_overrides(env_prefix))     # 3. MINI_AGENT_* 环境变量
layers.append(cli_overrides or {})                # 4. CLI 参数（未指定 = UNSET）
return Config.model_validate(recursive_merge(*layers))
```

三种覆盖写法，效果递进：

```bash
# 用 YAML 文件（一整份配置）
minimal --config my_config.yaml

# 用点号 key=value（覆盖单个字段，可重复）
minimal -c agent.max_steps=50 -c agent.cost_limit=5

# 用环境变量（__ 是嵌套分隔符）
MINI_AGENT_AGENT__MAX_STEPS=500 minimal
```

`agent.max_steps=50` 和 `MINI_AGENT_AGENT__MAX_STEPS=500` 都指向 `agent.max_steps` 这一个字段，只是来源不同、优先级不同。合并结果永远取**优先级最高那层**的值，而没有被覆盖的字段（比如 `tools.default_timeout`）保持默认不变——因为嵌套 dict 是递归合并，不是整层替换。

---

## 2. `recursive_merge` + `UNSET`：优先级链的基石

递归合并听起来简单，难点在于「**CLI 里没指定的参数，不能覆盖下层已有值**」。

想象没有 `UNSET` 哨兵：`main.py` 里 `--max-steps` 没传时是 `None`，如果直接把 `None` 合进去，就会把 `default.yaml` 里的 `250` 覆盖成 `None`。所以 `None` 不能用——何况 `max_time=None` / `cost_limit=None` 本来就是「关闭限制」的合法值，和「没指定」是两回事。

解决办法是引入一个独一无二的哨兵对象：

```python
UNSET = object()   # 「未指定」的占位符，recursive_merge 会跳过它
```

`recursive_merge(*dicts)` 的语义（照抄参考项目）：

```python
def recursive_merge(*dictionaries):
    result = {}
    for d in dictionaries:
        if d is None:                      # 整层为空 → 跳过
            continue
        for key, value in d.items():
            if value is UNSET:             # 值「未指定」→ 跳过，不覆盖
                continue
            if isinstance(result.get(key), dict) and isinstance(value, dict):
                result[key] = recursive_merge(result[key], value)  # 递归合并
            else:
                result[key] = value        # 后写优先
    return result
```

三个关键行为：

| 行为 | 例子 | 结果 |
|---|---|---|
| 后写优先 | `merge({"a":1}, {"a":2})` | `{"a": 2}` |
| 嵌套递归合并 | `merge({"a":{"b":1,"c":2}}, {"a":{"c":3}})` | `{"a": {"b":1, "c":3}}` |
| `UNSET` 跳过 | `merge({"a":1}, {"a":UNSET})` | `{"a": 1}` |

第 3 条正是 `cli.py` 里所有 CLI flag 默认值改 `None` 的原因——`_u(value)` 把 `None` 转成 `UNSET`，于是「没传 `--max-steps`」等价于「这一层对 `max_steps` 没意见」，下层的 `250` 得以保留。

测试见 [test_config_loading.py](../tests/test_config_loading.py) 的前 6 条（`test_recursive_merge_*`）。

---

## 3. Jinja2 模板渲染 vs `.format()`

Prompt 不能写死在代码里，但 prompt 里又有变量（任务描述、摘要内容）。有两种做法：

```python
# 做法 A：str.format()——引用不存在的变量不会报错
"Hello {task}".format(task="fix the bug")   # "Hello fix the bug"
"Hello {task}".format()                      # KeyError（缺参数时）

# 做法 B：Jinja2 + StrictUndefined——引用不存在的变量直接报错
Template("Hello {{ task }}", undefined=StrictUndefined).render(task="fix the bug")
Template("Hello {{ task }}", undefined=StrictUndefined).render()
# → jinja2.UndefinedError: 'task' is undefined   （而不是静默渲染成 "Hello "）
```

本项目的 prompt 放在 YAML 里：

```yaml
# config/default.yaml
agent:
  instance_template: |
    ## Task
    {{ task }}

    ## Workflow
    1. Explore ...
    2. Diagnose ...
```

运行时用 [render_template](../src/mini_agent/config/__init__.py) 渲染：

```python
render_template(agent_cfg.instance_template, task="修一下 bug")
```

**为什么用 Jinja2 而不是 `.format()`**：`.format()` 对「漏传变量」要么抛 `KeyError`（`{}` 裸调用）、要么静默放过（`{missing}` 不在 kwargs 里时其实会 KeyError，但 `.format_map` 可以漏）。Jinja2 的 `StrictUndefined` 让「模板引用了不存在的变量」变成**当场报错**——配置拼写错误在启动时就暴露，而不是等到 agent 跑了一半才把空串发给模型。

另一个附带好处：`{{ }}` 和 `.format()` 的 `{}` 不同，prompt 里可以安全出现 JSON 示例的 `{}`。而单行 YAML 里的 `{{ }}` 需要单引号包裹（`|` 字面块则不需要）。

---

## 4. pydantic v2 校验

YAML 是纯文本，合并出来的 dict 里任何一个字段都可能写错（`max_steps: oops`、`max_steps: "250"`）。pydantic 用类型注解在**合并完成那一刻**做校验：

```python
class AgentConfig(BaseModel):
    max_steps: int = 250          # 写错类型 → 当场 ValidationError
    cost_limit: float = 3.0

Config.model_validate(recursive_merge(*layers))
```

`build_config()` 最后一步就是 `Config.model_validate(...)`——任何一层写错，用户当场收到报错，而不是跑到 `agent.run()` 深处才炸。

模型分五块，和 YAML 顶层键一一对应（[config/models.py](../src/mini_agent/config/models.py)）：`ModelConfig` / `AgentConfig` / `ToolsConfig` / `CostConfig` / `EnvironmentConfig`。两个设计细节：

- **prompt 和工具启用名单必填**（无 pydantic 默认值）——它们只能来自 YAML，防止漏配；工具 schema 与 handler 在 `tools.py` 注册表中成对定义。
- **标量字段带默认值**（和 YAML 一致）——这样测试里裸写 `Model()` / `Agent()` 仍能构造，不用每次传完整 config。

---

## 5. secret 留在 `.env`，YAML 里只存「名字」

API key 是唯一**绝对不能**进 YAML 的东西——YAML 会被提交到 git、被复制、被打包进 wheel。所以：

```yaml
# config/default.yaml —— 只记环境变量「名」，不记 secret 本身
model:
  api_key_env: DEEPSEEK_API_KEY
```

真正的 key 留在 `.env`（已 gitignore），[model.py](../src/mini_agent/model.py) 运行时才 `os.environ[self.config.api_key_env]` 去取：

```python
api_key = os.environ[self.config.api_key_env]   # "DEEPSEEK_API_KEY" → 取 .env 里的值
```

环境变量扫描（`_env_var_overrides`）只认 `MINI_AGENT_` 前缀，`DEEPSEEK_API_KEY` 不带前缀、永远不会被扫进配置。切 OpenAI 只需要把 `api_key_env` 改成 `OPENAI_API_KEY`、`base_url` 去掉——一个字段的事（见 [faq.md](faq.md#为什么选-deepseek-而不是-openai/claude)）。

---

## 6. 覆盖一个配置项

以 `agent.max_steps` 为例，从易到难：

| 方式 | 命令 | 优先级 |
|---|---|---|
| CLI 参数 | `minimal --max-steps 50` | 最高 |
| 环境变量 | `MINI_AGENT_AGENT__MAX_STEPS=500 minimal` | 次高 |
| `--config key=value` | `minimal -c agent.max_steps=50` | 中 |
| `--config 文件` | `minimal --config my.yaml` | 较低 |
| 改 `default.yaml` | 直接改 [default.yaml](../src/mini_agent/config/default.yaml) | 最低（权威默认） |

单个字段用 `-c`，一整套环境（比如切 OpenAI + 调价格）写一个 YAML 文件更清晰。

### 内置两套工具配置

仓库内置两套工具配置，都基于同一套 `recursive_merge` 机制：

| 配置 | 工具集 | 用途 |
|---|---|---|
| [default.yaml](../src/mini_agent/config/default.yaml) | `bash` + `submit` + `read` + `edit` + `write` | **默认**——文件工具（读/改/写）默认启用 |
| [default_bash.yaml](../src/mini_agent/config/default_bash.yaml) | `bash` + `submit` | 旧路线——一切通过 bash（`cat`/`sed`/heredoc），`enabled` 名单只保留两个工具 |

```bash
minimal                        # 默认：5 工具
minimal --config default_bash  # 旧路线：仅 bash + submit
```

`default_bash.yaml` 只声明两个增量：把 `tools.enabled` 整个列表替换为 `[bash, submit]`，并把 `agent.system_prompt` 换回两工具版；工具 schema/handler 仍来自 `tools.py` 注册表，`model`/`cost`/`environment` 都从 `default.yaml` 继承。列表是整体替换，嵌套 dict 的其他字段仍按「后写优先 + 递归合并」继承。

工具选择采用白名单语义：只有 `tools.enabled` 中、且已经出现在 `TOOL_REGISTRY` 的名字才会发给模型并允许执行。比如直接切成两工具也可以写：

```bash
minimal -c 'tools.enabled=["bash","submit"]'
```

## 7. 对照代码

- 合并/渲染/校验：[config/__init__.py](../src/mini_agent/config/__init__.py) + [config/models.py](../src/mini_agent/config/models.py)
- 权威默认值：[config/default.yaml](../src/mini_agent/config/default.yaml)
- CLI 接线：[cli.py](../src/mini_agent/cli.py)（`build_config(...)` + `_u()`）
- 兼容入口：[main.py](../main.py)（转发到 `mini_agent.cli.main`）
- 测试：[test_config.py](../tests/test_config.py)（YAML 与 pydantic 默认一致）+ [test_config_loading.py](../tests/test_config_loading.py)（合并/解析/优先级/渲染/校验）
