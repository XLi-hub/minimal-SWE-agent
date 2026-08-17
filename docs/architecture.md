# 架构设计

## 核心思想

AI Agent 的本质就是一个循环：

```
deadline = time.monotonic() + max_time
try:
    for _ in range(max_steps):
        if time.monotonic() > deadline:              # 0. 超时检查
            exit_status = "max_time"; break
        if cost_limit and self.cost >= cost_limit:   # 0b. 成本上限检查
            exit_status = "cost_limit"; break
        if should_compress(messages, tools):         # 0c. 历史逼近上限 → 压缩
            messages[:] = compress(messages, model)  #     就地替换，保留别名
        response = model.query(messages, tools)      # 1. 模型思考
        self.cost += compute_cost(response)          # 1b. 累加本次成本（USD）
        if 模型调了submit: break                      # 2. 提交结果 → 退出
        _handle_tool_call(tc, messages, result)      # 3. 工具分发 + 执行
        # 循环 — 结果已在 _handle_tool_call 中写入 messages
finally:
    if output: save(output)                          # 无论怎么退出都落盘 .traj.json
# 超出 max_steps → exit_status="max_steps"
# 超出 max_time  → exit_status="max_time"
# 超出 cost_limit → exit_status="cost_limit"
```

核心逻辑从 `run()` 里的大段代码拆成了三个层次：
- `run()` — 循环控制 + 时间检查（约 35 行）
- `_handle_tool_call()` — 工具分发：submit / bash / unknown（约 45 行）
- `_truncate_output()` / `_decode_timeout_output()` — 输出处理（独立函数）

但怎么把这个循环拆成可维护、可测试的模块——这是架构要解决的问题。

## 模块职责

```
main.py                          # 入口：组装零件 + CLI 参数解析 + 合并配置
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
| `run()` | 循环控制：检查时间/步数/成本上限 → 逼近上限则压缩 → 查模型 → 累加成本 → 调分发器 |
| `_handle_tool_call()` | 工具分发：submit（退出）/ bash（执行）/ unknown（报错） |
| `_format_assistant_message()` | SDK 对象 → dict，供下轮 `model.query()` 使用 |
| `_truncate_output()` | 长输出截断，保留头尾 + WARNING 引导 |
| `_decode_timeout_output()` | 解码超时异常中的部分输出（bytes → str） |
| `serialize()` | 整场会话（messages + exit_status + submission + 成本）整理成结构化 dict |
| `save(path)` | `serialize()` 结果 JSON 序列化，落盘为 `.traj.json` |

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

`Agent.serialize()` 把整场会话整理成结构化 dict（`messages` 完整历史、`exit_status`、
`submission`，以及 `info.model_stats` 里的调用次数和累计成本），`save(path)` 再
`json.dumps` 落盘为 `.traj.json`。好处：

- **可回放**：`messages` 完整记录每一轮"模型思考 → 工具执行"，事后能复现推理链。
- **可分析**：`info.model_stats.instance_cost` / `api_calls` 让每次运行的成本一目了然。
- **保证落盘**：`run()` 用 `try/finally`，无论正常提交、超时、超步数还是报错，只要传了
  `output` 就一定会写文件。

```python
agent.run("fix the bug", output="run.traj.json")   # 结束后生成 run.traj.json
# CLI 等价：python main.py --task "fix the bug" -o run.traj.json
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
在累计成本超过阈值后停止，`exit_status` 记为 `"cost_limit"`。三者一起保证单次运行的
步数、时长、花费都有上限。

`compute_cost` 独立成 `cost.py`（只依赖 config 包）而不是塞进 `model.py`，是为了不破坏
`agent.py` 的延迟 import model 约定——`model.py` 在模块级 import openai + 执行
load_dotenv，agent 不想在 import 时就被迫加载它们。

## 参考：mini-swe-agent 怎么做的

参考项目用了完全一样的架构（Agent / Model / Environment 三件套），配置系统也是同款流程：

```
mini-swe-agent:
    main.py → 读 YAML 配置 → get_model(config) → get_environment(config) → get_agent(...)

我们的项目（现已对齐）:
    main.py → build_config(文件/环境变量/CLI) → get_environment(type, config) → Agent(Model(config.model), env, config)
```

两者都用 **YAML + pydantic 校验 + recursive_merge** 做配置，本质一样——都是依赖注入的另一种写法：不再在 `main.py` 里逐个 `argparse` 值插进构造函数，而是先把所有来源合并成一个 `Config`，再把它作为第三个依赖（和 Model、Environment 并列）注入各组件。详见 [config.md](config.md)。
