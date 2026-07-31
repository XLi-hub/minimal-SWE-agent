# minimal-SWE-agent

> 跟着 [minimal-agent.com](https://minimal-agent.com) 教程，从零构建一个终端 AI agent。

## 核心思想

AI Agent 的本质很简单：**一个大循环，把模型输出变成命令执行，再把结果喂回去。**

```
用户任务 → system prompt
              ↓
    ┌─────────────────────────────┐
    │  1. query LM       模型思考  │
    │  2. parse_action   提取命令  │
    │  3. execute_action 执行命令  │
    │  4. 结果喂回 LM，重复        │
    │  5. 遇到 "exit" 退出        │
    └─────────────────────────────┘
```

LM 是"大脑"，shell 是"手脚"，Agent 循环是连接两者的"神经系统"。

---

## 一步一步构建

### Step 1 — `model.py`：和 LM 对话

Agent 需要一个大脑。我们的实现用 DeepSeek API（OpenAI 兼容协议）：

```python
# src/mini_agent/model.py
class Model:
    def __init__(self):
        self._client = OpenAI(
            api_key=os.environ["DEEPSEEK_API_KEY"],
            base_url="https://api.deepseek.com",
        )

    def query(self, messages: list[dict]) -> str:
        response = self._client.chat.completions.create(
            model="deepseek-chat", messages=messages,
        )
        return response.choices[0].message.content
```

`messages` 是一个列表，每条消息有 `role`（system / user / assistant）和 `content`。这是 Agent 的"记忆"——LM 只能看到 `messages` 里的内容，所以我们要不断往里追加。

**关键细节：**

- `load_dotenv()` 从 `.env` 文件加载 `DEEPSEEK_API_KEY`，key 存在本地不进 git
- `httpx.Client` 显式传入，绕过 Clash 代理的 `socks://` 兼容问题
- 换用其他 LM（OpenAI / Ollama / LiteLLM）只需写一个同样有 `.query()` 方法的类

---

### Step 2 — `parser.py`：从 LM 回复中提取命令

LM 的输出是自然语言 + 代码块混在一起，比如：

```
好的，我来列出当前目录的文件：

```bash-action
ls -la
```
```

我们需要从中提取出 `ls -la`：

```python
# src/mini_agent/parser.py
import re

def parse_action(lm_output: str) -> str:
    matches = re.findall(
        r"```bash-action\s*\n(.*?)\n```", lm_output, re.DOTALL,
    )
    return matches[0].strip() if matches else ""
```

返回第一个匹配的命令字符串。如果 LM 没按要求格式输出，返回空字符串 `""`。

---

### Step 3 — `environment.py`：执行命令

用 `subprocess.run` 在本地 shell 执行，合并 stdout 和 stderr，30 秒超时：

```python
# src/mini_agent/environment.py
class Environment:
    def execute(self, command: str, timeout: int = 30) -> str:
        result = subprocess.run(
            command, shell=True, text=True,
            env=os.environ | ENV,     # ← ENV 关掉了交互式分页器
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=timeout,
        )
        return result.stdout
```

**为什么需要 `ENV`？** 很多命令（`man`、`git diff`）默认打开分页器等用户按键，会导致 agent 卡死：

```python
ENV = {
    "PAGER": "cat",              # 关掉分页器
    "MANPAGER": "cat",
    "LESS": "-R",                # less 也不交互
    "PIP_PROGRESS_BAR": "off",   # 关掉 pip 进度条
    "TQDM_DISABLE": "1",         # 关掉 tqdm 动画
}
```

---

### Step 4 — `agent.py`：把一切串起来

这是核心——Agent 循环：

```python
# src/mini_agent/agent.py
class Agent:
    def __init__(self, model, environment):
        self.model = model            # ← 注入，不硬编码
        self.environment = environment

    def run(self, task: str) -> list[dict]:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": task},
        ]

        while True:
            try:
                lm_output = self.model.query(messages)
                messages.append({"role": "assistant", "content": lm_output})

                action = parse_action(lm_output)

                if action == "exit":
                    break                         # 正常退出

                if action == "":
                    # LM 没按格式输出 → 提醒它
                    messages.append({"role": "user", "content": "Please use ```bash-action ... ``` format"})
                    continue                      # 跳过执行，重新问 LM

                output = self.environment.execute(action)
                messages.append({"role": "user", "content": output})

            except KeyboardInterrupt:
                break                             # Ctrl+C
            except Exception as e:
                messages.append({"role": "user", "content": f"Error: {e}"})
                # 不崩溃，把错误喂回 LM 让它自己修正

        return messages
```

**关键设计决策：**

| 场景 | 处理方式 | 为什么 |
|---|---|---|
| 正常命令 | 执行 → 结果喂回 LM | 标准循环 |
| `action == "exit"` | `break` 退出 | LM 主动结束 |
| `action == ""` | 提醒 + `continue` | LM 忘了格式，提醒比直接退更友好 |
| `execute_action` 超时 | `except Exception` 捕获 | 把错误告诉 LM，它可能换个命令重试 |
| `Ctrl+C` | `break` 退出 | 用户想停 |

**`continue` 的作用：** 跳过本轮剩余的 `execute_action` 和 `messages.append`，立刻回到 `while True` 顶部，用新增的提醒消息重新查询 LM。没有 `continue` 的话代码会继续执行空命令——这既没意义又会把空字符串污染进对话历史。

---

### Step 5 — `main.py`：入口

```python
# main.py
from src.mini_agent.agent import Agent
from src.mini_agent.model import Model
from src.mini_agent.environment import Environment

if __name__ == "__main__":
    task = input("Task: ")
    agent = Agent(Model(), Environment())
    agent.run(task)
```

---

## 项目结构

```
minimal-SWE-agent/
├── src/mini_agent/
│   ├── model.py          # Model      — .query(messages) → str
│   ├── parser.py         # parse_action — 正则提取 bash-action 块
│   ├── environment.py    # Environment — .execute(command) → str
│   └── agent.py          # Agent      — 主循环，注入 model + environment
├── tests/
│   ├── test_model.py     # 3 个 — API 调用测试（需联网）
│   ├── test_parser.py    # 5 个 — 正则提取边界情况
│   ├── test_environment.py # 7 个 — 命令执行 + 环境变量覆盖
│   └── test_agent.py     # 3 个 — 循环逻辑 + 异常恢复（mock）
├── main.py               # 入口
├── run_tests.py           # 一键运行全部测试
├── .env                   # API key（不进 git）
└── .gitignore
```

---

## 环境准备

```bash
# Python 3.10+
conda create -n minimal-SWE-agent python=3.10
conda activate minimal-SWE-agent

# 依赖
pip install openai pytest httpx python-dotenv
```

## 配置 API Key

复制 `.env` 文件到项目根目录：

```
DEEPSEEK_API_KEY=你的key
```

`.env` 已在 `.gitignore` 中，不会被提交。

---

## 运行测试

```bash
python run_tests.py
```

共 18 个测试，覆盖四个模块。

---

## 启动 Agent

```bash
python main.py
```

输入任务（如 `List the files in the current directory`），agent 循环执行命令直到 LM 输出 `exit`。

---

## 架构：依赖注入

```
            main.py
               │
        Agent(model, env)
          │           │
    Model.query()  Environment.execute()
          │           │
     DeepSeek API   subprocess.run
```

- `Agent` **不认识**具体的 `Model` 和 `Environment` 实现
- 它只要求传入的对象有 `.query()` 和 `.execute()` 方法
- 换 OpenAI、换 Docker 环境——改 `main.py` 一行构造函数即可
- 测试时用 `MagicMock()` 代替真实对象，不需要 patch 模块级函数

---

## 已知问题

| 问题 | 原因 | 解决 |
|---|---|---|
| `httpx` 报 `Unknown scheme: socks://` | Clash 设了 `ALL_PROXY=socks://` | `model.py` 显式传 `http_client`，只用 `HTTP_PROXY` |
| `PYTHONPATH` 混入 ROS 的 pytest 插件 | ROS 的 `launch_testing` 与项目无关 | `run_tests.py` 自动清理 |

---

## 已完成

- [x] Step 1: 50 行原型 — LM → parse → execute 循环
- [x] Step 2: 健壮性 — 异常处理、格式校验、环境变量
- [x] Step 3: 模块化 — Model / Environment / Agent 三个类，依赖注入

## 参考

- [minimal-agent.com](https://minimal-agent.com) — 原教程
- [mini-swe-agent](https://github.com/swe-agent/mini-swe-agent) — 生产级实现
