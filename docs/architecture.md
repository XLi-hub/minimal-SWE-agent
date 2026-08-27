# 架构设计

## 核心思想

AI Agent 的本质就是一个循环。参考 mini-swe-agent，本项目用**异常驱动控制流**：退出条件
（提交 / 无工具调用 / 超限）不再用 `return` 标志层层返回，而是抛一个 `AgentExit` 子类异常，
由 `run()` 统一捕获后填 `exit_status` / `submission` 并退出循环。

```
try:
    while True:
        try:
            step()                              # 一轮：查上限 → 压缩 → 查询 → 执行
        except AgentExit as e:                  # 正常退出（异常驱动）
            exit_status = e.exit_status
            submission = e.submission
            break
        except KeyboardInterrupt:               # Ctrl+C
            exit_status = "interrupted"; break
        except Exception as e:                  # 未处理错误 → 记录诊断并以 error 退出
            messages.append({"role": "user", "content": f"Error: {e}"})
finally:
    if output: save(output)                     # 落盘 .traj.json + .events.jsonl
```

`step()` 的关键路径是：轮次前检查预算 → 必要时压缩 → 查询模型 → 再检查本次查询是否已经耗尽时间/成本 → 执行工具。第二次检查不重复判断 `max_steps`，因为当前查询已经合法进入这一轮；它只阻止越时或越费后继续执行有副作用的工具。

| 退出条件 | 抛出的异常 |
|---|---|
| 模型调了 submit | `Submitted` |
| 模型无 tool_calls | `NoToolCalls` |
| 达到步数上限 | `MaxSteps` |
| 超过时长上限 | `MaxTime` |
| 超过成本上限 | `CostLimit` |
| Ctrl+C | 直接捕获 `KeyboardInterrupt` |

核心逻辑从 `run()` 里的大段代码拆成了几个方法，职责单一：
- `run()` — 只做循环控制 + 异常捕获（`AgentExit`/`KeyboardInterrupt`/`Exception`）
- `step()` — 一轮编排：查上限 → 压缩 → 查询 → 执行
- `query()` — 查模型 + 累加成本 + 追加 assistant 消息（无工具调用时抛 `NoToolCalls`）
- `execute_actions()` — 逐条执行工具调用；遇到 `submit` 后跳过剩余 handler，但仍为批次中每个 tool call 记录 observation，再统一退出
- `_check_limits()` / `_maybe_compress()` — 上限检查 / 上下文压缩
- 工具 schema/handler 注册、权限分发与输出处理（`truncate_output` / `decode_timeout_output`）集中在 `tools.py`；输出同时受行数和字符数预算约束

但怎么把这个循环拆成可维护、可测试的模块——这是架构要解决的问题。

## 模块职责

```
src/mini_agent/cli.py            # CLI：参数解析 + 合并配置 + 组装零件
src/mini_agent/__main__.py       # python -m mini_agent 的入口
main.py                          # 兼容入口，转发到 mini_agent.cli
  │
Agent(model, env, config)        # 循环逻辑：什么时候查模型、什么时候执行
  │           │           │
Model        Environment  Config  # 接口 + 配置：只定义方法签名 / 只描述数据
.query()     .execute()
  │           │
DeepSeek     Local / Docker      # 实现：具体的 API 调用 / shell 执行

cost.py                          # 纯函数 compute_cost(response, config) → USD（只依赖 config 包）
context.py                       # 纯函数 token 估算 + LLM 摘要（只依赖 config 包）
config/                          # 配置包：YAML 加载 + recursive_merge + pydantic 校验 + Jinja2 渲染
```

### 为什么分模块而不是一个文件

**一个文件写完** → 改一行可能影响全局，测试只能"端到端"跑（必须联网 + 真的执行命令）

**分模块** → 每个模块可以独立测试、独立替换：

| 模块 | 可以单独 | 怎么测 |
|---|---|---|
| `Model` | 换 OpenAI / Ollama / Claude | Mock `httpx.Client`，不需要联网 |
| `Environment` | 换 local / Docker / Singularity | Mock `subprocess.run`，不需要真执行 |
| `Agent` | 换不同的循环策略 | Mock Model + Environment，不需要 API |
| `Config` | 换工具定义 / system prompt | 纯数据验证，不涉及任何 IO |
| `context` | 换不同的压缩/摘要策略 | Mock Model 返回固定摘要文本，不调 API |

Agent 内部方法分工：

| 方法 | 职责 |
|---|---|
| `run()` | 循环控制 + 异常捕获：`while True: step()`，捕获 `AgentExit` / `KeyboardInterrupt` / 通用 `Exception` |
| `step()` | 一轮编排：查询前检查全部预算，查询后再检查时间/成本，最后才执行工具 |
| `query()` | 查模型一次 + 累加成本 + 追加 assistant 消息（无工具调用则抛 `NoToolCalls`） |
| `execute_actions(msg)` | 逐条执行工具；`submit` 后不再执行 handler，但补齐其余 tool responses 后才抛 `Submitted` |
| `_check_limits()` | 检查步数/时长/成本上限，超限抛 `MaxSteps` / `MaxTime` / `CostLimit` |
| `_maybe_compress()` | 历史逼近上下文上限时压缩（就地切片赋值，保持 `messages` 别名） |
| `serialize()` | 模型 context、完整 events、exit status、submission 与成本整理成结构化 dict |
| `save(path)` | context/结果落盘为 `.traj.json`，完整事件落盘为 `.events.jsonl` |

工具 schema 与 handler 成对注册在 `tools.py` 的 `TOOL_REGISTRY`。`get_enabled_tool_schemas` 根据配置生成模型可见列表，`execute_tool_call` 用同一名单检查执行权限；消息格式化和输出处理（`format_assistant_message`、`truncate_output`、`decode_timeout_output`）也留在该模块。

这里维护一个重要协议不变量：assistant 声明的每个 `tool_call_id` 都必须按原顺序得到一条 `role=tool` 响应。模型可能在同一批次里同时返回 `submit` 和其他调用；执行器会保留 submit 的结果，跳过其后的副作用操作，并为这些调用写入确定性的 `Skipped` observation。这样保存的 trajectory 可以继续被 OpenAI-compatible API 校验、回放和分析。

## 依赖注入

这是本项目最重要的设计模式。一句话：**不在类内部创建依赖，而是从外部传进来**。

```python
# 坏：Agent 内部写死了 Model 和 Environment
class Agent:
    def __init__(self):
        self.model = Model()                # 想换模型？改代码
        self.environment = LocalEnvironment()  # 想换环境？改代码

# 好：依赖从外部传入
class Agent:
    def __init__(self, model, environment):
        self.model = model                  # 外面传什么用什么
        self.environment = environment
```

好处：
1. **可替换**：`Agent(Model(), DockerEnvironment(...))` 和 `Agent(MockModel(), MockEnv())` 用的是同一个 Agent 类
2. **可测试**：测试时传 `MagicMock()`，不需要真的调 API 或执行命令
3. **解耦**：Agent 不知道 Model 内部用什么 API，Model 不知道 Environment 怎么执行命令

## 接口 vs 实现

```python
# 接口（ABC）—— 定义"能做什么"
class Environment(ABC):
    @abstractmethod
    def execute(self, command: str, timeout: int = 30) -> str: ...
    def cleanup(self) -> None: ...

# 实现 —— 具体"怎么做"
class LocalEnvironment(Environment):
    def execute(self, command, timeout=30):
        return subprocess.run(command, shell=True, ...).stdout

class DockerEnvironment(Environment):
    def execute(self, command, timeout=30):
        return subprocess.run(["docker", "exec", ...]).stdout
```

Agent 只和 `Environment` 接口打交道，不关心是 local 还是 docker。这叫**面向接口编程**。

## 轨迹保存 + 成本统计

循环跑完之后，除了返回结果，还有两件"副产品"需要记录：轨迹和成本。

### 轨迹（trajectory）

`Agent` 同时维护两层记录：`messages` 是可能被压缩的模型 context view，`events` 是只追加
的原始消息和压缩事件。`serialize()` 将两者与 `exit_status`、`submission`、调用次数和累计
成本整理成结构化 dict；`save(path)` 把主数据和事件分别落盘。好处：

- **可审计**：`.events.jsonl` 完整记录每一轮“模型消息 → 工具执行”和压缩发生时点；
- **上下文真实**：`.traj.json.messages` 保留结束时模型真正继续使用的压缩视图；
- **可分析**：`info.model_stats.instance_cost` / `api_calls` 让每次运行的成本一目了然。
- **保证落盘**：`run()` 用 `try/finally`，无论正常提交、超时、超步数还是报错，只要传了
  `output` 就一定会写文件。

```python
agent.run("fix the bug", output="run.traj.json")   # 同时生成 run.events.jsonl
# CLI 等价：minimal --task "fix the bug" -o run.traj.json
```

### 成本（cost）

成本不靠外部库，直接用模型返回的 `response.usage`（`prompt_tokens` / `completion_tokens` /
`prompt_tokens_details.cached_tokens`）× 每百万 token 单价（USD）累加。单价定义在
[config/default.yaml](../src/mini_agent/config/default.yaml) 的 `cost` 段（`price_input_per_1m`
等），[cost.py](../src/mini_agent/cost.py) 的 `compute_cost()` 负责算单次调用，缓存命中比未命中便宜一个数量级：

```python
# 100 万输入 token，其中 50 万命中缓存，无输出
compute_cost(response)  # ≈ 0.14 * 0.5 + 0.0028 * 0.5 = 0.0714 USD
```

`cost_limit`（默认 3.0，`0`/`None` 关闭）是第三种"兜底"——像 `max_steps`/`max_time` 一样，
在累计成本达到阈值后停止，`exit_status` 记为 `"cost_limit"`。成本是在模型响应返回后才能
精确计算的，所以 Agent 会在派发工具前立即复查；即使这次查询刚好越过预算，也不会继续
执行文件写入或 shell 命令。

`compute_cost` 独立成 `cost.py`（只依赖 config 包）而不是塞进 `model.py`，是为了不破坏
`agent.py` 的延迟 import model 约定——`model.py` 在模块级 import openai + 执行
load_dotenv，agent 不想在 import 时就被迫加载它们。

## 参考：mini-swe-agent 怎么做的

参考项目用了完全一样的架构（Agent / Model / Environment 三件套），配置系统也是同款流程：

```
mini-swe-agent:
    cli.py → 读 YAML 配置 → get_environment(config) → Agent(Model(config.model), env, config)

我们的项目（现已对齐）:
    cli.py → build_config(文件/环境变量/CLI) → get_environment(type, config) → Agent(Model(config.model), env, config)
```

两者都用 **YAML + pydantic 校验 + recursive_merge** 做配置，本质一样——都是依赖注入的另一种写法：不再在 CLI 入口里逐个 `argparse` 值插进构造函数，而是先把所有来源合并成一个 `Config`，再把它作为第三个依赖（和 Model、Environment 并列）注入各组件。详见 [config.md](config.md)。
