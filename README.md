# minimal-SWE-agent

> 不理解 AI agent 为什么能自动修 bug？[核心循环](src/mini_agent/agent.py) 只有 ~100 行 Python，不依赖任何框架——你看到的每一行代码都在做一件事。

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

## 配置

所有可调项（模型、prompt、工具 schema、价格、环境、步数/时长/成本上限…）都外置在
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
YAML 里只记环境变量「名」`model.api_key_env`（默认 `DEEPSEEK_API_KEY`）。合并、优先级、
模板渲染、校验的完整讲解见 [docs/config.md](docs/config.md)。

## 项目结构

```
src/mini_agent/
├── cli.py                    # CLI 参数解析、配置合并和 Agent 组装
├── __main__.py               # python -m mini_agent 入口
├── agent.py                  # Agent 循环（异常驱动）— 查询 LM → 执行工具 → 循环
├── tools.py                  # 工具分发（bash/read/edit/write/submit）+ 消息格式化 + 输出截断
├── cost.py                   # 成本计算（token → USD）
├── context.py                # 上下文压缩（token 估算 + LLM 增量摘要）
├── model.py                  # DeepSeek API 封装（OpenAI 兼容协议）
├── config/                   # 配置包（YAML + pydantic + 模板渲染）
│   ├── __init__.py            #   recursive_merge / build_config / render_template
│   ├── models.py              #   pydantic v2 模型（Config + 5 个子配置）
│   └── default.yaml           #   权威默认值（prompt / 工具 schema / 价格 / 环境）
└── environments/             # 执行环境（可插拔）
    ├── __init__.py            #   Environment ABC + get_environment() 工厂
    ├── local.py               #   LocalEnvironment — 本机 shell
    └── docker.py              #   DockerEnvironment — 容器内执行

tests/
├── test_agent.py               # Agent 循环（异常驱动）+ 截断 + submit + 异常 + 轨迹 + 成本 + 压缩（62 个测试）
├── test_config.py              # 工具 schema + system prompt + 默认值 + 定价（35 个测试）
├── test_config_loading.py      # 配置合并/优先级/渲染/校验（20 个测试）
├── test_config_read_edit.py    # 两套内置工具配置：5 工具默认 + 2 工具旧路线（6 个测试）
├── test_cost.py                # 成本计算 compute_cost（7 个测试，全部 mock）
├── test_context.py             # 上下文压缩纯函数（17 个测试，全部 mock）
├── test_model.py               # API 调用（8 个测试，全部 mock）
├── test_tools.py               # 工具分发 read/edit/write + apply_edit + 输出截断（14 个测试）
├── test_environment.py         # 本地环境（15 个测试）
├── test_environments_init.py   # 工厂函数 + ABC + 注册表（10 个测试）
├── test_docker.py              # Docker 环境（14 个测试，含跳过逻辑）
├── test_integration.py         # Agent+真Shell（11 个测试，mock Model）
└── test_e2e.py                 # 端到端测试（2 个测试，默认跳过，需 API key）
```

## 架构

```
minimal / python -m mini_agent  ──►  mini_agent.cli  ──►  Agent(model, env)
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

Agent 给模型五个工具：

| 工具 | 用途 |
|---|---|
| `bash(command, lines?, timeout?)` | 执行 shell 命令，可选限制返回行数和超时秒数 |
| `read(path)` | 读文件内容，带行号 |
| `edit(path, old_string, new_string)` | 精确替换文件中唯一一处 `old_string` |
| `write(path, content)` | 创建或覆盖文件 |
| `submit(output)` | 提交最终结果（patch / 答案 / 总结） |

模型主动调用 `submit` 退出，而非隐式停止。`run()` 返回结构化结果：

```python
result = agent.run("fix the bug")
# {"exit_status": "submitted", "submission": "diff --git ...", "messages": [...]}
# exit_status: "submitted" | "no_tool_calls" | "max_steps" | "max_time" | "cost_limit" | "interrupted" | "error"
```

## 成本统计

Agent 自动从每次模型调用的 `response.usage`（`prompt_tokens` / `completion_tokens` /
`prompt_tokens_details.cached_tokens`）按 USD 单价累计成本，缓存命中比未命中便宜得多：

- 单价定义在 [config/default.yaml](src/mini_agent/config/default.yaml) 的 `cost` 段（`price_input_per_1m` 等，可按需改）。
- 累计成本写入轨迹的 `info.model_stats.instance_cost`（USD）。
- 用 `cost_limit` 设定上限（默认 3.0，`0` 或 `None` 关闭）：累计成本超过就停止，`exit_status` 为 `"cost_limit"`。

```python
agent.run("fix the bug", output="run.traj.json", cost_limit=1.5)
# 或 CLI：minimal --task "..." -o run.traj.json --cost-limit 1.5
```

## 上下文压缩

Agent 循环每步都会往 `messages` 追加模型思考和工具输出，历史会无限增长——250 步的
bash 输出轻松超过 `deepseek-chat` 的 64K 上下文。当历史逼近上限时，Agent 会用一次
**无工具的 LLM 调用**把中间的旧对话折叠成一条结构化摘要，只保留 system prompt、
原始任务、以及最近 N 轮 verbatim：

- 触发条件：`count_tokens >= 0.8 × (context_window − reserve)`（默认窗口 64000、阈值 0.8、预留 2000 token 给下一轮回复）。
- 保留策略：永不压缩 system prompt 和原始任务；最近 `keep_last_n_turns`（默认 4）轮原样保留，只压中间。
- 摘要方式：折叠式增量——已有摘要 + 新对话 → 新摘要，结构化标题（任务/文件改动/关键决策/错误与测试结果/当前状态/下一步）。
- 原子性：`assistant(tool_calls)` 和紧随其后的 `tool` 结果作为一个整体保留或丢弃，绝不拆开（否则 OpenAI/DeepSeek 会 HTTP 400）。
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

## 运行测试

```bash
# 日常 — 跳过 E2E（Docker 集成测试自动检测 daemon，无 Docker 时自动跳过）
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/ -v -p no:anyio -m "not e2e"

# E2E 测试 — 真调 DeepSeek API（2 个，花钱，偶尔跑一次）
python -m pytest tests/ -v -m e2e

# 全量 — 包括 E2E（221 个测试）
python -m pytest tests/ -v -p no:anyio

# 只跑单元测试（跳过 Docker 集成 + E2E）
python -m pytest tests/ -v -p no:anyio -m "not e2e" -k "not test_docker_echo and not test_docker_pwd and not test_docker_env and not test_docker_command"
```

Docker 集成测试在检测不到 Docker daemon 时自动跳过。E2E 测试在 `.env` 未配置 `DEEPSEEK_API_KEY` 时自动跳过。

**测试分层**：179 单元 + 40 集成（含 Docker）+ 2 E2E = 221 总计。

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
