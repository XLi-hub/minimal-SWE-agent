# minimal-SWE-agent

> 从零构建一个 AI 编程 agent —— 理解 SWE-agent 的核心原理。

## 这是什么

一个最小化的 AI agent：LM（大模型）作为"大脑"，shell 作为"手脚"，Agent 循环连接两者。模型自主决定执行什么命令、读取什么文件、何时提交结果。

```
用户: "修一下 utils.py 的 bug"
  │
  ▼
Agent 循环:
  LM 思考 → bash(cat utils.py)
          → 拿到输出，继续思考
          → bash(sed ... 修复)
          → bash(git diff) 拿到 patch
          → submit(output=patch)
```

## 快速开始

```bash
# 安装
pip install -e ".[dev]"

# 配置 API Key（创建 .env 文件）
echo 'DEEPSEEK_API_KEY=你的key' > .env

# 日常使用（本地环境）
python main.py

# 使用 Docker 隔离环境
python main.py --env docker --image python:3.11-slim
```

## 项目结构

```
src/mini_agent/
├── agent.py                  # Agent 循环 — 查询 LM → 执行工具 → 循环
├── config.py                 # SYSTEM_PROMPT, BASH_TOOL, SUBMIT_TOOL 定义
├── model.py                  # DeepSeek API 封装（OpenAI 兼容协议）
└── environments/             # 执行环境（可插拔）
    ├── __init__.py            #   Environment ABC + get_environment() 工厂
    ├── local.py               #   LocalEnvironment — 本机 shell
    └── docker.py              #   DockerEnvironment — 容器内执行

tests/
├── test_agent.py              # Agent 循环 + 截断 + submit（19 个测试）
├── test_config.py             # 工具 schema + system prompt（13 个测试）
├── test_model.py              # API 调用（7 个测试，全部 mock）
├── test_environment.py        # 本地环境（9 个测试）
└── test_docker.py             # Docker 环境（10 个测试，含跳过逻辑）
```

## 架构

```
main.py  ──►  Agent(model, env)
                 │          │
            Model          Environment (ABC)
           .query()        .execute()  .cleanup()
                 │          │          │
            DeepSeek     Local        Docker
```

三个组件通过**依赖注入**组装，各自只依赖接口：

- `Model.query(messages, tools) → OpenAI response`
- `Environment.execute(command, timeout) → str`

换 OpenAI、换 Docker、写 mock 测试——改构造函数即可，Agent 代码不动。

## 工具

Agent 给模型两个工具：

| 工具 | 用途 |
|---|---|
| `bash(command, lines?)` | 执行 shell 命令，可选限制返回行数 |
| `submit(output)` | 提交最终结果（patch / 答案 / 总结） |

模型主动调用 `submit` 退出，而非隐式停止。`run()` 返回结构化结果：

```python
result = agent.run("fix the bug")
# {"exit_status": "submitted", "submission": "diff --git ...", "messages": [...]}
```

## 环境分流

```bash
# 本地
python main.py

# Docker 隔离
python main.py --env docker --image python:3.11-slim --cwd /workspace
```

用工厂函数 `get_environment(name, **kwargs)` 创建，加新环境只需写一个类 + 注册一行：

```python
# environments/__init__.py
_MAPPING = {
    "local": LocalEnvironment,
    "docker": DockerEnvironment,
    # "singularity": SingularityEnvironment,  ← 加一个就行
}
```

## 运行测试

```bash
# 全量（55 个测试）
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/ -v -p no:anyio

# 只跑单元测试（跳过 Docker 集成测试）
python -m pytest tests/ -v -k "not docker_echo and not docker_pwd and not docker_env and not docker_command"
```

Docker 集成测试在检测不到 Docker daemon 时自动跳过。

## 学习文档

项目代码力求简洁，但很多设计决策值得展开：

- **[架构设计](docs/architecture.md)** — 为什么分模块、依赖注入、接口设计
- **[知识点索引](docs/concepts.md)** — ABC、工厂模式、mock 测试、function calling 概念解释
- **[工具调用演进](docs/tool-calling.md)** — 从文本解析到 OpenAI function calling
- **[环境分流](docs/environment.md)** — 注册表模式、local vs docker、如何加新环境

## 参考

- [minimal-agent.com](https://minimal-agent.com) — 入门教程
- [mini-swe-agent](https://github.com/swe-agent/mini-swe-agent) — 生产级实现，本项目结构参考了它
- [SWE-bench](https://www.swebench.com/) — 自动化编程评测基准
