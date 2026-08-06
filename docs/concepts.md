# 知识点索引

按在本项目中出现的顺序组织。每个概念配有"在这个项目里怎么用的"例子。

---

## 抽象基类 (ABC — Abstract Base Class)

Python 的 `abc.ABC` + `@abstractmethod`。作用是**定义接口**——规定子类必须实现哪些方法。

```python
from abc import ABC, abstractmethod

class Environment(ABC):
    @abstractmethod
    def execute(self, command: str) -> str:
        """子类必须实现这个方法"""
        ...

# LocalEnvironment 和 DockerEnvironment 都必须有 .execute()
# 没有的话，实例化时 Python 直接报错
```

**和 Java/C++ 的区别**：Python 的 ABC 是运行时检查（实例化时才报错），不像 Java 的 interface 是编译时检查。但目的相同——强迫子类遵守约定。

**在本项目中**：[environments/__init__.py](../src/mini_agent/environments/__init__.py)

---

## 依赖注入 (Dependency Injection)

不在类内部 `new` 对象，而是从外部传进来。

```python
# 不用依赖注入
class Agent:
    def __init__(self):
        self.model = Model()  # 写死了

# 用依赖注入
class Agent:
    def __init__(self, model, environment):  # 传什么用什么
        self.model = model
```

**为什么叫"注入"**：依赖（Model、Environment）被"注入"到 Agent 里，而不是 Agent 自己去找。

**好处**：换实现、写测试都方便。测试时传 mock，生产时传真实对象，Agent 代码不动。

---

## Mock（模拟对象）

测试时用假对象代替真对象。Python 的 `unittest.mock.MagicMock` 可以假装成任何对象。

```python
from unittest.mock import MagicMock

# 造一个假 Model，它的 .query() 返回我们预先准备好的响应
model = MagicMock()
model.query.return_value = fake_response

# Agent 不知道 model 是假的——它只调用 .query()
agent = Agent(model, MagicMock())
result = agent.run("test")
```

**为什么需要 mock**：
1. 真调 API 要花钱 + 等网络
2. 真执行命令可能删文件
3. Mock 让我们只测 Agent 循环逻辑，不测外部依赖

**在本项目中**：`tests/test_agent.py` 全部用 mock，`tests/test_model.py` mock 了 `httpx.Client`。

---

## 工厂函数 (Factory)

一个函数，根据参数创建并返回不同类型的对象。

```python
def get_environment(name: str, **kwargs) -> Environment:
    if name == "local":
        return LocalEnvironment(**kwargs)
    elif name == "docker":
        return DockerEnvironment(**kwargs)
    else:
        raise ValueError(f"Unknown: {name}")
```

等价写法——注册表模式（本项目用的）：

```python
_MAPPING = {
    "local": LocalEnvironment,
    "docker": DockerEnvironment,
}

def get_environment(name, **kwargs):
    return _MAPPING[name](**kwargs)
```

**为什么用注册表**：加新类型只需要在 dict 里加一行，不用改 if/elif 链。

**在本项目中**：[environments/__init__.py](../src/mini_agent/environments/__init__.py)

---

## OpenAI Function Calling (工具调用)

模型不只返回文本，还能返回一个"我想调这个函数"的结构化请求。

```
用户: "列出当前目录的文件"
模型: 不直接说话，而是返回 →
      {name: "bash", arguments: {command: "ls -la"}}
Agent: 执行 ls → 把结果发回模型
模型: {name: "submit", arguments: {output: "完成"}}
Agent: 退出
```

```python
# 工具定义（告诉模型你可以做什么）
BASH_TOOL = {
    "type": "function",
    "function": {
        "name": "bash",
        "description": "Execute a bash command...",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "lines": {"type": "integer"},
            },
            "required": ["command"],
        },
    },
}

# 查模型时把工具列表传过去
response = client.chat.completions.create(
    model="deepseek-chat",
    messages=messages,
    tools=[BASH_TOOL, SUBMIT_TOOL],  # ← 模型会从这里面选
)
```

**和文本解析的区别**：

| | 文本解析（旧） | Function Calling（新） |
|---|---|---|
| 模型输出 | 自由文本 | 结构化的 JSON |
| 提取命令 | 正则 `re.findall` | `response.choices[0].message.tool_calls` |
| 可靠性 | 模型可能不按格式写 | 100% 准确（模型被训练来遵守 schema） |

**在本项目中**：[config.py](../src/mini_agent/config.py) 定义工具，[agent.py](../src/mini_agent/agent.py) 处理调用。

---

## subprocess.run

Python 标准库，用来在操作系统里执行命令。

```python
import subprocess

# 执行 ls -la，等它跑完，拿到输出
result = subprocess.run(
    "ls -la",
    shell=True,              # 用 shell 解析命令（支持管道、重定向）
    text=True,               # 输出转成字符串而非 bytes
    capture_output=True,     # 捕获 stdout + stderr
    timeout=30,              # 超时就杀进程
)
print(result.stdout)         # 命令的输出
print(result.returncode)     # 退出码（0 = 成功）
```

**在本项目中**：`LocalEnvironment.execute()` 的核心就是这个。

---

## Docker

操作系统级别的容器隔离。理解它的最简单方式——把它当成"一个独立的微型 Linux"。

```bash
# 启动一个 Ubuntu 容器，在里面执行 echo
docker run --rm ubuntu:22.04 echo "hello"

# 启动一个后台容器，然后进入执行命令
docker run -d --rm --name mybox python:3.11 sleep 2h
docker exec mybox python -c "print(1+1)"

# 设环境变量
docker exec -e FOO=bar mybox bash -c 'echo $FOO'
```

**本项目 DockerEnvironment 做的事**：

```python
# __init__: 启动容器
docker run -d --rm --name mini-agent-abc123 -w / python:3.11-slim sleep 2h

# execute: 在容器里执行
docker exec -w / mini-agent-abc123 bash -lc "ls -la"

# cleanup: 停止并删除
docker stop mini-agent-abc123
```

**在本项目中**：[environments/docker.py](../src/mini_agent/environments/docker.py)

---

## SWE-bench

一个自动化评测基准（benchmark）。给 Agent 一个 GitHub issue（如"这个函数在 x=0 时崩溃"），让它自动修 bug。修完后跑原仓库的测试——通过则得分。

流程：

```
Agent 拿到 issue → 在 Docker 容器里查代码 → 修改 → git diff 生成 patch
→ submit(patch) → 评测系统拿 patch 去打原仓库 → 跑测试 → 通过/不通过
```

本项目设计 `submit(output=patch)` 就是为了后续对接 SWE-bench。

---

## argparse

Python 标准库，用于解析命令行参数。

```python
import argparse

parser = argparse.ArgumentParser()
parser.add_argument("--env", default="local", choices=["local", "docker"])
parser.add_argument("--image", default="python:3.11-slim")
args = parser.parse_args()

print(args.env)    # "docker"
print(args.image)  # "python:3.11-slim"
```

等价效果：

```bash
python main.py --env docker --image ubuntu:22.04
```

**在本项目中**：[main.py](../main.py)
