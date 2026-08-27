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
    model="deepseek-v4-flash",
    messages=messages,
    tools=[BASH_TOOL, SUBMIT_TOOL],  # ← 模型会从这里面选
    # 关闭思考模式；项目 YAML 中对应 model_kwargs.extra_body
    extra_body={"thinking": {"type": "disabled"}},
)
```

**和文本解析的区别**：

| | 文本解析（旧） | Function Calling（新） |
|---|---|---|
| 模型输出 | 自由文本 | 结构化的 JSON |
| 提取命令 | 正则 `re.findall` | `response.choices[0].message.tool_calls` |
| 可靠性 | 模型可能不按格式写 | 100% 准确（模型被训练来遵守 schema） |

**在本项目中**：每个工具的 schema 和 handler 成对定义在 [tools.py](../src/mini_agent/tools.py) 的 `TOOL_REGISTRY`；[config/default.yaml](../src/mini_agent/config/default.yaml) 的 `tools.enabled` 只选择启用哪些工具。[agent.py](../src/mini_agent/agent.py) 把启用工具的 schema 发给模型，分发器也用同一名单检查执行权限。

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
minimal --env docker --image ubuntu:22.04
```

**在本项目中**：[cli.py](../src/mini_agent/cli.py)；`main.py` 只是兼容入口。

---

## pydantic

Python 最流行的**数据校验库**。核心思想：用类型注解声明数据结构，pydantic 自动校验——拼写错误、类型错误全在创建对象那一刻就报错。

```python
from pydantic import BaseModel

# 声明结构
class AgentConfig(BaseModel):
    model_name: str
    max_steps: int = 250      # 默认值
    temperature: float = 0.0

# 构造时校验
config = AgentConfig(model_name="deepseek-v4-flash")      # ✅ 正常
config = AgentConfig(model_name=123)                        # ❌ 当场报错: must be str
config = AgentConfig(max_steps="oops")                      # ❌ 当场报错: must be int
config = AgentConfig(model_name="gpt", extra_field=True)    # ❌ 当场报错: extra field not allowed
```

**为什么叫 pydantic**：受 Rust 的 `serde`（serialize/deserialize）启发——同一套 schema 既做校验又做序列化/反序列化。

**和 dataclass 的区别**：

```python
# dataclass: 类型注解只是提示，不做运行时校验
from dataclasses import dataclass
@dataclass
class Config:
    max_steps: int = 250

Config(max_steps="not a number")  # ✅ 不会报错！运行时才发现问题

# pydantic: 类型注解会被真正校验
from pydantic import BaseModel
class Config(BaseModel):
    max_steps: int = 250

Config(max_steps="not a number")  # ❌ ValidationError
```

**mini-swe-agent 为什么用 pydantic**：有几十个配置项（模型名、API key、超时、成本上限、镜像名、环境变量…），YAML 配置文件的字段全靠 pydantic 校验——用户写错了当场知道，而不是跑到深层代码时才炸。

**本项目现在也用**：配置外置到 YAML 后，配置项从 3 个涨到二十多个（模型、prompt、启用工具、压缩参数、价格、环境…），argparse + kwargs 扛不住了——于是换成和参考项目同款的 **pydantic v2**。`build_config()` 合并完各来源后，最后一步 `Config.model_validate(...)` 一次性校验所有字段。详见 [config.md](config.md#4-pydantic-v2-校验)。

---

## recursive_merge（递归合并）+ UNSET 哨兵

把多个 dict **递归**合并成一个，后写的赢。和 `{**a, **b}` 的区别在于：嵌套 dict 是逐层合并，而不是整层替换。

```python
# 普通合并：a 的 "agent" 整层被 b 覆盖，max_steps 之外的字段全丢
{**{"agent": {"max_steps": 250, "cost_limit": 3.0}}, **{"agent": {"max_steps": 50}}}
# → {"agent": {"max_steps": 50}}          # cost_limit 没了！

# 递归合并：只覆盖 max_steps，cost_limit 保留
recursive_merge({"agent": {"max_steps": 250, "cost_limit": 3.0}},
                {"agent": {"max_steps": 50}})
# → {"agent": {"max_steps": 50, "cost_limit": 3.0}}
```

配套的 `UNSET = object()` 哨兵解决「**未指定**」和「**合法的 None**」的冲突——CLI 里没传的参数用 `UNSET` 占位，合并时跳过，不覆盖下层已有值；而 `max_time=None`（关闭限制）是真实值，照常合并。

**在本项目中**：[config/__init__.py](../src/mini_agent/config/__init__.py) 的 `recursive_merge` / `UNSET` / `build_config`，是「default.yaml < --config < 环境变量 < CLI」优先级链的基石。详见 [config.md](config.md#2-recursive_merge--unset优先级链的基石)。

---

## Jinja2 模板渲染

在字符串里留 `{{ 变量 }}` 占位符，运行时填充。和 `str.format()` 的最大区别：`StrictUndefined` 让「引用了不存在的变量」**当场报错**，而不是静默渲染成空串。

```python
from jinja2 import Template, StrictUndefined

tpl = Template("## Task\n{{ task }}", undefined=StrictUndefined)
tpl.render(task="修一下 bug")   # "## Task\n修一下 bug"
tpl.render()                    # UndefinedError: 'task' is undefined
```

**在本项目中**：`agent.instance_template` / `agent.summary_prompt` 都存在 [default.yaml](../src/mini_agent/config/default.yaml) 里，运行时由 [render_template](../src/mini_agent/config/__init__.py) 渲染。详见 [config.md](config.md#3-jinja2-模板渲染-vs-format)。

---

## 单元测试 (Unit Test)

测试**一个函数或一个类**，不依赖任何外部资源（网络、文件系统、数据库、API）。

```python
# 单元测试：只测 truncate_output 这个函数
def test_long_output_is_truncated():
    result = truncate_output("line1\nline2\n...\nline200", max_lines=100)
    assert "truncated" in result
```

关键技巧是 **mock**：把函数依赖的一切外部对象都替换成假的。

```python
model = MagicMock()                          # 假模型
model.query.return_value = fake_response     # 不调 API
env = MagicMock()                            # 假环境
env.execute.return_value = "fake output"     # 不执行命令
agent = Agent(model, env)                    # Agent 不知道是假的
```

特点：**最快（毫秒级）、最便宜（0 元）、数量最多**。

**在本项目中**：test_agent.py（mock Model + Environment）、test_config.py（YAML 与 pydantic 默认一致）、test_config_loading.py（合并/优先级/渲染/校验）、test_model.py（mock httpx）、test_cost.py（mock usage 对象，验证 token → USD 换算）

---

## 集成测试 (Integration Test)

测试**两个以上模块之间的配合**，部分真实、部分 mock。

```python
# 集成测试：真 LocalEnvironment + 假 Model
# 验证 Agent 循环和 shell 执行之间的配合
def test_executes_tool_call_then_exits():
    model = MagicMock()
    model.query.side_effect = [bash_response, text_response]
    agent = Agent(model, LocalEnvironment())   # ← env 是真的
    result = agent.run("ls")
```

特点：**秒级、比单元测试慢但比 E2E 快**。

**在本项目中**：`test_integration.py`（真 shell + 假 Model，11 个）、`test_environment.py`（真 shell 执行，15 个）、`test_docker.py`（真 Docker daemon，14 个，无 daemon 时自动跳过）

---

## 端到端测试 (E2E — End to End)

**整个系统从头到尾，不 mock 任何东西**。用户怎么用就怎么测。

```
单元测试:  Agent ──► mock Model ──► 假数据          测一段
集成测试:  Agent ──► 真 Env ──► 真 shell           测两段
E2E:       Agent ──► 真 Model ──► 真 API ──► 真 shell  测整条链
```

```python
@pytest.mark.e2e
def test_simple_echo_task():
    agent = Agent(Model(), LocalEnvironment())   # 全都是真的
    result = agent.run("echo hello and submit")
    assert result["exit_status"] == "submitted"
```

特点：**最慢（秒~分钟）、花钱（调 API 要按 token 计费）、数量最少**。

**为什么 E2E 数量最少**：单元测试和集成测试已经把逻辑验证完了，E2E 只回答一个问题——"模型真的理解我们的 tool schema 吗？真的会调 bash 和 submit 吗？"这是 mock 永远验证不了的。

**在本项目中**：test_e2e.py（2 个，默认跳过，手动 `-m e2e` 才跑）

---

## Happy Path vs Error Path

每个功能的测试都有两条路：

```
Happy Path（开心路径） — 一切正常，没有意外
Error Path（异常路径） — 某个环节出问题了
```

```python
# Happy Path — 命令正常执行
env.execute.return_value = "hello world"
result = agent.run("echo hello")
assert result["exit_status"] == "submitted"

# Error Path — 命令执行失败
env.execute.side_effect = RuntimeError("disk full")
result = agent.run("echo hello")
# agent 不应该崩溃，应该把错误发给模型处理
assert "disk full" in result["messages"][-2]["content"]
```

**新人常犯的错误**：只测 happy path。因为写测试的时候脑子里想的也是"正常怎么用"。但生产环境里 bug 几乎全来自 error path——happy path 早被你自己手动跑过了。

**检查方法**——对着代码一行行扫：
- 每个 `if` → true 和 false 都测了？
- 每个 `except` → 造一个会抛的情况？
- 每个 `raise` → 确认真的会抛？

**在本项目中**：Agent 测试里有 `test_recovers_from_execution_error`、`test_keyboard_interrupt_returns_interrupted_status`、`test_model_query_exception_is_appended_as_user_message`——都是在测 error path。

---

## 测试金字塔 (Test Pyramid)

经典的分层策略——越底层数量越多、速度越快：

```
       ╱‾‾‾‾‾╲         E2E           最少    慢/贵    "整条链路通了吗?"
      ╱       ╲
     ╱ 集成    ╲       集成测试       适量    中级     "两个模块配合对了吗?"
    ╱           ╲
   ╱  单元测试   ╲     单元测试       最多    快/免费  "每个函数行为对吗?"
  ‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾
```

金字塔倒过来就是灾难——E2E 最多、单元测试最少。那样的测试又慢又贵，没人愿意跑，最后就没人跑了。

**在本项目中**：测试保持“单元最多、集成其次、付费 E2E 最少”的比例；具体数量交给 pytest 收集结果展示，文档不硬编码会持续漂移的数字。
