# minimal-SWE-agent

> 不理解 AI agent 为什么能自动修 bug？这个项目把 SWE-agent 的核心拆到 ~200 行 Python，不依赖任何框架——你看到的每一行代码都在做一件事。

## 这是什么

一个最小化的 AI agent：LM（大模型）作为"大脑"，shell 作为"手脚"，Agent 循环连接两者。模型自主决定执行什么命令、读取什么文件、何时提交结果。

**和参考项目 [mini-swe-agent](https://github.com/swe-agent/mini-swe-agent) 的区别**：mini 是生产工具（配置系统、多模型商、多环境后端、TUI），目标是跑 SWE-bench 高分。本项目是学习工具——保留相同的核心架构（Agent/Model/Environment 三件套），但把每个模块都写到最简，让读者能一眼看到底。

```
用户: "修一下 utils.py 的 bug"
  │
  ▼
Agent 循环 (最多 250 步):
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

**试试这些任务**：

```bash
# 探索型：让 agent 理解项目结构
python main.py --task "列出 src/ 下所有 .py 文件并概述每个模块的职责"

# 编码型：让 agent 写代码并测试
python main.py --task "在 /tmp 下创建一个 Python 模块，实现斐波那契数列，并写一个简单的测试"

# 调试型：给一个故意有 bug 的文件，让 agent 修
echo 'def add(a, b): return a - b  # bug: should be +' > /tmp/buggy.py
python main.py --task "修一下 /tmp/buggy.py 的 bug"
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
├── test_agent.py               # Agent 循环 + 截断 + submit + 异常（30 个测试）
├── test_config.py              # 工具 schema + system prompt + 默认值（18 个测试）
├── test_model.py               # API 调用（7 个测试，全部 mock）
├── test_environment.py         # 本地环境（9 个测试）
├── test_environments_init.py   # 工厂函数 + ABC + 注册表（10 个测试）
├── test_docker.py              # Docker 环境（10 个测试，含跳过逻辑）
├── test_integration.py         # Agent+真Shell（11 个测试，mock Model）
└── test_e2e.py                 # 端到端测试（2 个测试，默认跳过，需 API key）
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
| `bash(command, lines?, timeout?)` | 执行 shell 命令，可选限制返回行数和超时秒数 |
| `submit(output)` | 提交最终结果（patch / 答案 / 总结） |

模型主动调用 `submit` 退出，而非隐式停止。`run()` 返回结构化结果：

```python
result = agent.run("fix the bug")
# {"exit_status": "submitted", "submission": "diff --git ...", "messages": [...]}
# exit_status: "submitted" | "no_tool_calls" | "max_steps" | "interrupted" | "error"
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
# 日常 — 跳过 E2E（Docker 集成测试自动检测 daemon，无 Docker 时自动跳过）
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/ -v -p no:anyio -m "not e2e"

# E2E 测试 — 真调 DeepSeek API（2 个，花钱，偶尔跑一次）
python -m pytest tests/ -v -m e2e

# 全量 — 包括 E2E（97 个测试）
python -m pytest tests/ -v -p no:anyio

# 只跑单元测试（跳过 Docker 集成 + E2E）
python -m pytest tests/ -v -p no:anyio -m "not e2e" -k "not test_docker_echo and not test_docker_pwd and not test_docker_env and not test_docker_command"
```

Docker 集成测试在检测不到 Docker daemon 时自动跳过。E2E 测试在 `.env` 未配置 `DEEPSEEK_API_KEY` 时自动跳过。

**测试分层**：74 单元 + 21 集成（含 Docker）+ 2 E2E = 97 总计。

## 学习文档

项目代码力求简洁，但很多设计决策值得展开：

- **[架构设计](docs/architecture.md)** — 为什么分模块、依赖注入、接口设计
- **[知识点索引](docs/concepts.md)** — ABC、工厂模式、mock 测试、function calling 概念解释
- **[工具调用演进](docs/tool-calling.md)** — 从文本解析到 OpenAI function calling
- **[环境分流](docs/environment.md)** — 注册表模式、local vs docker、如何加新环境
- **[测试策略](docs/testing.md)** — 为什么分三层、每层测什么、mock 的边界
- **[常见问题](docs/faq.md)** — 为什么这样设计、数字怎么定的、和 mini-swe-agent 的区别

## 参考

- [minimal-agent.com](https://minimal-agent.com) — 入门教程
- [mini-swe-agent](https://github.com/swe-agent/mini-swe-agent) — 生产级实现，本项目结构参考了它
- [SWE-bench](https://www.swebench.com/) — 自动化编程评测基准
