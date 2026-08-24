# minimal-SWE-agent

> 不理解 AI agent 为什么能自动修 bug？从[核心循环](src/mini_agent/agent.py)开始读：不依赖 Agent 框架，模型调用、工具协议、执行环境和退出条件都能沿着代码直接追踪。

## 这是什么

一个最小化的 AI agent：LM（大模型）作为"大脑"，shell 作为"手脚"，Agent 循环连接两者。模型自主决定执行什么命令、读取什么文件、何时提交结果。

**和参考项目 [mini-swe-agent](https://github.com/swe-agent/mini-swe-agent) 的区别**：mini 是生产工具（配置系统、多模型商、多环境后端、TUI），目标是跑 SWE-bench 高分。本项目是学习工具——保留相同的核心架构（Agent/Model/Environment 三件套），但把每个模块都写到最简，让读者能一眼看到底。

这个项目刻意不把“最小”理解成“只能跑通 demo”。核心代码同时展示了几类真实工程问题的处理：

- **协议正确性**：assistant 的每个 tool call 都必须有对应 observation，批次提前提交也不会留下无法回放的半截轨迹。
- **资源治理**：步数、时间、成本和工具输出都有预算；超长单行输出也不能挤爆模型上下文。
- **可替换架构**：Model / Environment / Config 通过依赖注入组装，本地、Docker、mock 和 OpenAI-compatible provider 互不耦合。
- **失败可诊断**：每次运行都可保存 trajectory，包含退出状态、消息、模型调用数、成本和错误信息。
- **安全的交付流程**：默认测试不选择真实模型 E2E，不会因为开发机恰好存在 API key 就误产生费用；CI 再把真实 Docker 层独立排除。

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
echo 'OPENAI_API_KEY=你的key' > .env

# 日常使用（本地环境；安装后推荐）
minimal

# 不安装 console script 时，也可以这样启动
python -m mini_agent

# 兼容旧用法
python main.py

# 使用 Docker 隔离环境
minimal --env docker --image python:3.11-slim
```

**试试这些任务**：

```bash
# 探索型：让 agent 理解项目结构
minimal --task "列出 src/ 下所有 .py 文件并概述每个模块的职责"

# 编码型：让 agent 写代码并测试
minimal --task "在 /tmp 下创建一个 Python 模块，实现斐波那契数列，并写一个简单的测试"

# 调试型：给一个故意有 bug 的文件，让 agent 修
echo 'def add(a, b): return a - b  # bug: should be +' > /tmp/buggy.py
minimal --task "修一下 /tmp/buggy.py 的 bug"
```

## 作为 Python 库使用

不经过 CLI 也可以直接组装 `Model`、`LocalEnvironment` 和 `Agent`。下面是
[hello_world.py](examples/hello_world.py) 的核心用法：

```python
from mini_agent.agent import Agent
from mini_agent.environments.local import LocalEnvironment
from mini_agent.model import Model

agent = Agent(Model(), LocalEnvironment())
result = agent.run(
    """
    Use the bash tool to run this command without creating any files:
    python -c 'print("Hello, world!")'
    After confirming its exact output, call submit with `Hello, world!`.
    """.strip(),
    max_steps=5,
)

if result["exit_status"] != "submitted":
    raise RuntimeError(result["exit_status"])

print(result["submission"])
```

安装项目并配置 `OPENAI_API_KEY` 后运行：

```bash
python examples/hello_world.py
```

这个例子刻意显式展示三个可替换组件的依赖注入；它执行的命令只打印文本，不会创建
业务文件。`max_steps=5` 可防止模型没有按预期提交时无限循环。

## 配置

所有运行配置（模型、prompt、启用工具、价格、环境、步数/时长/成本上限…）都外置在
[config/default.yaml](src/mini_agent/config/default.yaml)，由四层来源合并，后写优先：

```
default.yaml  <  --config 文件/key=value  <  MINI_AGENT_* 环境变量  <  CLI 参数
```

```bash
# 用 YAML 文件覆盖一整套配置
minimal --config my_config.yaml

# 用点号 key=value 覆盖单个字段（可重复）
minimal -c agent.max_steps=50 -c agent.cost_limit=5

# 用环境变量覆盖（__ 为嵌套分隔符）
MINI_AGENT_AGENT__MAX_STEPS=500 minimal
```

API key **绝不**进 YAML（YAML 会被 git 提交/打包）——它留在 `.env`（已 gitignore），
YAML 里只记环境变量「名」`model.api_key_env`（默认 `OPENAI_API_KEY`）。合并、优先级、
模板渲染、校验的完整讲解见 [docs/config.md](docs/config.md)。

## 项目结构

```
src/mini_agent/
├── cli.py                    # CLI 参数解析、配置合并和 Agent 组装
├── __main__.py               # python -m mini_agent 入口
├── agent.py                  # Agent 循环（异常驱动）— 查询 LM → 执行工具 → 循环
├── tools.py                  # 工具注册表（schema + handler）+ 权限分发 + 输出处理
├── cost.py                   # 成本计算（token → USD）
├── context.py                # 上下文压缩（token 估算 + LLM 增量摘要）
├── model.py                  # OpenAI-compatible API 封装
├── config/                   # 配置包（YAML + pydantic + 模板渲染）
│   ├── __init__.py            #   recursive_merge / build_config / render_template
│   ├── models.py              #   pydantic v2 模型（Config + 6 个子配置）
│   └── default.yaml           #   权威运行配置（prompt / 启用工具 / 价格 / 环境）
└── environments/             # 执行环境（可插拔）
    ├── __init__.py            #   Environment ABC + get_environment() 工厂
    ├── local.py               #   LocalEnvironment — 本机 shell
    └── docker.py              #   DockerEnvironment — 容器内执行

tests/
├── test_agent.py               # Agent 循环、协议不变量、轨迹、成本与压缩
├── test_tools.py               # 工具注册、权限分发、文件操作与输出预算
├── test_config*.py             # 配置合并、优先级、模板和语义校验
├── test_model.py               # Provider 适配层（mock API）
├── test_environment*.py        # 本地环境、Docker 和环境工厂
├── test_integration.py         # Agent + 真 shell（mock Model）
├── test_cli.py                 # CLI 参数、退出码和资源生命周期
├── benchmarks/                 # SWE-bench runner / evaluation adapter
└── test_e2e.py                 # 显式 opt-in 的付费 API 端到端测试
```

## 架构

```
minimal / python -m mini_agent  ──►  mini_agent.cli  ──►  Agent(model, env)
                 │          │
            Model          Environment (ABC)
           .query()        .execute()  .cleanup()
                 │          │          │
      OpenAI-compatible Local        Docker
```

三个组件通过**依赖注入**组装，各自只依赖接口：

- `Model.query(messages, tools) → OpenAI response`
- `Environment.execute(command, timeout) → {output, returncode, exception_info}`

换模型供应商、换 Docker、写 mock 测试——改配置/构造函数即可，Agent 代码不动。

## 工具

Agent 给模型五个工具：

| 工具 | 用途 |
|---|---|
| `bash(command, lines?, timeout?)` | 执行 shell 命令；返回内容受行数 + 字符数双预算保护 |
| `read(path)` | 读文件内容并加行号；超大文件按字符预算保留头尾 |
| `edit(path, old_string, new_string)` | 精确替换文件中唯一一处 `old_string` |
| `write(path, content)` | 创建或覆盖文件 |
| `submit(output)` | 提交最终结果（patch / 答案 / 总结） |

模型主动调用 `submit` 退出，而非隐式停止。`run()` 返回结构化结果：

```python
result = agent.run("fix the bug")
# {"exit_status": "submitted", "submission": "diff --git ...", "messages": [...]}
# exit_status: "submitted" | "no_tool_calls" | "max_steps" | "max_time" | "cost_limit" | "interrupted" | "error"
```

如果同一模型响应里包含多个 tool calls，Agent 会按顺序为每个 `tool_call_id` 写入结果。遇到 `submit` 后不再执行其后的副作用操作，但会给它们记录明确的 skipped observation，保证 trajectory 始终符合 OpenAI-compatible 消息协议。

## 成本统计

Agent 自动从每次模型调用的 `response.usage`（`prompt_tokens` / `completion_tokens` /
`prompt_tokens_details.cached_tokens`）按 USD 单价累计成本，缓存命中比未命中便宜得多：

- 单价定义在 [config/default.yaml](src/mini_agent/config/default.yaml) 的 `cost` 段（`price_input_per_1m` 等，默认 0；接入具体供应商后按需配置）。
- 累计成本写入轨迹的 `info.model_stats.instance_cost`（USD）。
- 配好供应商单价后，用 `cost_limit` 设定上限（默认 3.0，`0` 或 `None` 关闭）：累计成本达到上限就停止，`exit_status` 为 `"cost_limit"`，且不会继续派发本轮工具。

```python
agent.run("fix the bug", output="run.traj.json", cost_limit=1.5)
# 或 CLI：minimal --task "..." -o run.traj.json --cost-limit 1.5
```

## 上下文压缩

Agent 循环每步都会往 `messages` 追加模型思考和工具输出，历史会无限增长——250 步的
bash 输出轻松超过常见模型上下文窗口。当历史逼近上限时，Agent 会用一次
**无工具的 LLM 调用**把中间的旧对话折叠成一条结构化摘要，只保留 system prompt、
原始任务、以及最近 N 轮 verbatim：

- 触发条件：`count_tokens >= 0.8 × (context_window − reserve)`（默认窗口 64000、阈值 0.8、预留 2000 token 给下一轮回复）。
- 保留策略：永不压缩 system prompt 和原始任务；最近 `keep_last_n_turns`（默认 4）轮原样保留，只压中间。
- 摘要方式：折叠式增量——已有摘要 + 新对话 → 新摘要，结构化标题（任务/文件改动/关键决策/错误与测试结果/当前状态/下一步）。
- 原子性：`assistant(tool_calls)` 和紧随其后的 `tool` 结果作为一个整体保留或丢弃，绝不拆开（否则 OpenAI-compatible API 通常会 HTTP 400）。
- 失败兜底：摘要调用失败时静默跳过本轮，继续用完整历史，不影响主循环。
- 成本计入：摘要本身也是一次 API 调用，其 token 用量计入 `instance_cost` 和 `api_calls`，与主循环查询一视同仁。

```python
agent.run("fix the bug")   # 默认 64K 窗口，长任务会自动压缩
# 或 CLI 强制触发（极小窗口）：
# minimal --context-window 2000 --keep-last-n-turns 2 --task "修一下 bug"
```

压缩是会话内行为，不写任何持久化记忆文件。详见 [上下文压缩](docs/context-compression.md)。

## 环境分流

```bash
# 本地
minimal

# Docker 隔离
minimal --env docker --image python:3.11-slim --cwd /workspace
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

## SWE-bench

评测依赖是可选的。安装后可以先跑一个实例，再逐步扩大并发：

```bash
pip install -e ".[bench]"

minimal-swebench \
  --subset verified --split test \
  --instance 0 \
  --model gpt-4o-mini \
  --output runs/smoke

minimal-swebench \
  --subset verified --split test \
  --slice 0:20 --workers 4 \
  --model gpt-4o-mini \
  --output runs/verified-20
```

runner 会按实例选择官方 Docker image，保存原子更新的 `preds.json`、harness 使用的
`preds.jsonl`、状态概览和逐实例 trajectory，并支持 `--redo-existing` / `--retry-failed`。
生成结束后可选安装 `.[eval]`，用官方 harness 评分：

```bash
pip install -e ".[eval]"
minimal-swebench-eval runs/verified-20/preds.jsonl \
  --dataset verified --split test --workers 4 \
  --run-id verified-20 --report-dir runs/verified-20/reports
```

完整说明见 [SWE-bench 文档](docs/swebench.md)。

## 运行测试

```bash
# 日常 — 项目 conda 环境；不调用真实模型 API
conda run -n minimal-SWE-agent env PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python -m pytest tests/ -q -p no:anyio -m "not e2e"

# 普通 CI 层：同时排除需要真实 Docker daemon 的测试
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/ -q \
  -p no:anyio -m "not e2e and not docker"

# E2E — 必须显式 opt-in，真调 OpenAI-compatible API，会产生费用
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/ -v \
  -p no:anyio -m e2e

# 裸 pytest 也安全：pyproject.toml 默认排除 e2e
python -m pytest
```

Docker 集成测试在检测不到 daemon 时自动跳过。E2E 即使发现 API key 也不会被默认选择；这条“显式付费”边界由 pytest 配置和 CI 共同保证。测试数量不在文档中硬编码，避免新增覆盖后说明悄悄过期。

## 学习文档

项目代码力求简洁，但很多设计决策值得展开：

- **[架构设计](docs/architecture.md)** — 为什么分模块、依赖注入、接口设计
- **[配置外置](docs/config.md)** — YAML / 环境变量替代硬编码，`recursive_merge` + 模板渲染 + pydantic 校验
- **[知识点索引](docs/concepts.md)** — ABC、工厂模式、mock 测试、function calling 概念解释
- **[工具调用演进](docs/tool-calling.md)** — 从文本解析到 OpenAI function calling
- **[环境分流](docs/environment.md)** — 注册表模式、local vs docker、如何加新环境
- **[上下文压缩](docs/context-compression.md)** — 为什么历史会无限增长、LLM 摘要怎么触发、tool-call 原子性约束
- **[测试策略](docs/testing.md)** — 为什么分三层、每层测什么、mock 的边界
- **[常见问题](docs/faq.md)** — 为什么这样设计、数字怎么定的、和 mini-swe-agent 的区别

## 参考

- [minimal-agent.com](https://minimal-agent.com) — 入门教程
- [mini-swe-agent](https://github.com/swe-agent/mini-swe-agent) — 生产级实现，本项目结构参考了它
- [SWE-bench](https://www.swebench.com/) — 自动化编程评测基准
