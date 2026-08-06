# 架构设计

## 核心思想

AI Agent 的本质就是一个循环：

```
while True:
    output = model.query(messages, tools)    # 1. 模型思考
    if 模型调了submit: break                  # 2. 提交结果 → 退出
    result = environment.execute(command)     # 3. 执行命令
    messages.append(result)                  # 4. 结果喂回去
```

但怎么把这个循环拆成可维护、可测试的模块——这是架构要解决的问题。

## 模块职责

```
main.py                          # 入口：组装零件 + CLI 参数解析
  │
Agent(model, env)                # 循环逻辑：什么时候查模型、什么时候执行
  │           │
Model        Environment (ABC)   # 接口：只定义方法签名，不关心实现
.query()     .execute()
  │           │
DeepSeek     Local / Docker      # 实现：具体的 API 调用 / shell 执行
```

### 为什么分四个模块而不是一个文件

**一个文件写完** → 改一行可能影响全局，测试只能"端到端"跑（必须联网 + 真的执行命令）

**分模块** → 每个模块可以独立测试、独立替换：

| 模块 | 可以单独 | 怎么测 |
|---|---|---|
| `Model` | 换 OpenAI / Ollama / Claude | Mock `httpx.Client`，不需要联网 |
| `Environment` | 换 local / Docker / Singularity | Mock `subprocess.run`，不需要真执行 |
| `Agent` | 换不同的循环策略 | Mock Model + Environment，不需要 API |
| `Config` | 换工具定义 / system prompt | 纯数据验证，不涉及任何 IO |

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

## 参考：mini-swe-agent 怎么做的

参考项目用了完全一样的架构（Agent / Model / Environment 三件套），但多了一层配置系统：

```
mini-swe-agent:
    main.py → 读 YAML 配置 → get_model(config) → get_environment(config) → get_agent(...)

我们的项目:
    main.py → argparse 解析 → get_environment(name) → Agent(Model(), env)
```

参考项目用 YAML + pydantic 做配置校验，我们目前用 argparse + kwargs。本质一样——都是依赖注入的不同写法。
