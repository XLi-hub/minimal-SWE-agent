# 常见问题

---

## 为什么不直接用 SWE-agent 或 mini-swe-agent？

那两个是**产品**，目标是跑 SWE-bench 高分、支持生产环境。本项目是**学习工具**——保留完全相同的架构（Agent/Model/Environment 三件套），但删掉了所有"多余"的东西：

| mini-swe-agent | minimal-SWE-agent |
|---|---|
| YAML + pydantic 配置系统 | YAML + pydantic（同款，但更小） |
| litellm / openrouter / portkey 多商 | 只用 openai SDK，连 DeepSeek |
| 正则解析 `bash` 块 | OpenAI function calling |
| Docker/Singularity/Bubblewrap/... 多后端 | Local + Docker 两个 |
| Textual TUI / Interactive / CLI 三种界面 | 一个 `main.py` |
| 1000+ commits，13 个月迭代 | ~48 commits，多轮迭代 |

去掉了这么多东西之后，你才能一眼看到 agent 循环到底在做什么。读懂了这个项目，再去看 mini-swe-agent 的源码就轻松了。

---

## 为什么用 tool calling 而不是让模型自由输出文本？

本项目最初 v1 也是正则解析（见 [tool-calling.md](tool-calling.md)），和 mini-swe-agent 现在的做法一样——模型输出自由文本，agent 用 `re.findall` 提取 `bash` 块。

但正则方案的问题在于：

1. **格式不稳定**：模型有时忘了用 `bash` 标记，有时用了别的标记（如 `bash` 代替 `bash-action`）
2. **需要重试**：解析失败 → 构造 user message 提醒模型 → 浪费一轮对话
3. **错误信息不统一**：每个模型的输出习惯不同，调试很痛苦

Function calling 让模型返回**结构化 JSON**——`tool_calls` 数组里每个元素有 `name` 和 `arguments`。不存在"解析失败"这个概念。代价是模型必须支持 tool calling API（DeepSeek、OpenAI、Claude 都支持），且不能用于完全不兼容的模型。

**本项目选 tool calling 是因为**：作为教学项目，直接用 API 原生支持的方式比手写一个脆弱的解析器更"干净"——读者不需要理解正则的实现细节就能看懂 agent 循环的核心逻辑。

如果要做生产系统，用哪种看场景：tool calling 更可靠但绑定 API 格式；正则解析更通用但需要更好的容错和修复逻辑。

---

## 为什么每个命令独立执行而不是保持 shell 会话？

这个设计是跟着 mini-swe-agent 走的——每步操作是一个独立的 `subprocess.run`，而不是开一个 shell 进程一直对话。

**好处**：

1. **隔离性强**：一个命令卡死（`cat` 一个大文件），超时杀了就行，不影响后续操作
2. **沙箱友好**：`docker exec X bash -c "cmd"` 也是每次独立执行——换个后端换 `subprocess.run` 就行
3. **线性历史**：每一步的输出 → user message，没有隐藏的 shell 状态（cd 到了哪里、设置了什么 env var）

**代价**：`cd` 和 `export` 不能跨步生效。模型很快就学会了——它会在每一步命令前显式 `cd /path && actual_command`。

详细讨论见 [mini-swe-agent FAQ](https://mini-swe-agent.com/latest/faq/#why-no-shell-session)。

---

## `max_steps=250`、`timeout=30`、`max_time=1800` 怎么定的？

三个数字都不是"算出来的"，是从经验中选的：

**`max_steps=250`**：来自 mini-swe-agent 的 SWE-bench 配置。SWE-bench 任务通常需要 10-50 步（查代码、定位 bug、修改、git diff、submit）。250 足够完成任何合理的任务，同时防止死循环把 API 费用烧光。你可以用 `--max-steps` 覆盖：

```bash
python main.py --max-steps 50    # 限制步数
python main.py --max-steps 500   # 给复杂任务更多步
```

除了 `--max-steps`，也可以用 `-c agent.max_steps=50` 或 `MINI_AGENT_AGENT__MAX_STEPS=500` 覆盖同一个字段。所有配置项的覆盖方式统一见 [config.md](config.md)。

**`timeout=30`**：默认 30 秒对 `ls`、`cat`、`git diff` 足够。对于 `pip install`、`git clone`、长编译——模型可以在 tool call 里指定更长的时间：

```python
# 模型可以做这种事：
bash(command="pip install torch", timeout=120)
```

**v4 重要变化**：超时**不杀进程**。改用 `Popen + communicate(timeout=...)`，超时后进程继续跑，模型收到部分输出 + `[STILL RUNNING]` 提示。模型可以加 timeout 等完成，也可以 `kill` 掉重来。详见 [tool-calling.md](tool-calling.md#超时处理不杀进程)。

**`max_time=1800`**（30 分钟）：防止模型在 timeout 上不断翻倍（30→60→120→...）把时间耗光。`max_steps` 管步数，`max_time` 管总时长，双重兜底。传 `--max-time 0` 或 `max_time=None` 可关闭限制。

注意这和很多 agent 用 `sleep` 在命令里等待不同——sleep 阻塞的是 shell 进程，timeout 是 harness 层的硬限制。两个独立计算。

---

## `cost_limit=3.0` 怎么定的？

和 `max_steps`/`max_time` 一样，是"兜底上限"而不是精确预算。默认 3 美元对一次 SWE-bench 风格的修 bug 任务（读代码 → 改一行 → 跑测试，通常 10-50 步）绰绰有余，同时防止死循环把 API 费用烧穿。

成本怎么算的：

1. 每次调用后从 `response.usage` 拿 token 数（输入命中缓存 / 未命中 / 输出三档）。
2. 乘以 config 里的 USD 单价（`cost.price_input_per_1m`、`cost.price_input_cache_hit_per_1m`、`cost.price_output_per_1m`，定义在 [default.yaml](../src/mini_agent/config/default.yaml)）。
3. 累计值写进轨迹的 `info.model_stats.instance_cost`。

DeepSeek 2026/08/17 起改成了峰谷计价，这里的单价是固定默认值（注释里标了来源），可按需改。`cost_limit` 传 `0` 或 `None` 就关闭限制（CLI 用 `--cost-limit 0`）。

## 轨迹 .traj.json 里有什么？

每次运行 `agent.run(..., output="run.traj.json")`（CLI 用 `-o`）都会把整场会话落盘：

```json
{
  "trajectory_format": "mini-agent-0.1",
  "messages": [ ... ],                          // 完整的模型思考 + 工具执行历史
  "info": {
    "exit_status": "submitted",                 // 怎么结束的
    "submission": "diff --git ...",             // 最终结果
    "model_stats": {
      "api_calls": 12,                          // 调了几次模型
      "instance_cost": 0.0428                   // 花了多少钱（USD）
    }
  }
}
```

用途：回放推理链（`messages` 完整）、统计成本、调试（看模型卡在哪一步）。`run()` 用 `try/finally` 保证——即使 `max_steps` / `max_time` / `cost_limit` / 报错退出，只要传了 `output` 就一定写文件。

---

## 测试分三层？是不是过度设计了？

不是。三层各自的职责不同，跑测试的人也不同：

```
E2E (2个)     → 我（开发者）：提交代码前跑一次，验证模型真的理解工具schema
集成 (40个)   → CI：每次 push 自动跑，验证模块配合没坏
单元 (179个)  → 写代码时随手跑：改一行，跑一秒，确认没坏
```

如果只有 E2E，跑一次花 30 秒 + 花钱，你就不跑了。如果只有单元测试，mock 的假输出可能和真输出行为不一致（我们在集成测试里就抓过一个——`echo` 会解释反斜杠但 `printf '%s'` 不会）。

更多细节见 [testing.md](testing.md)。

---

## 为什么选 DeepSeek 而不是 OpenAI/Claude？

1. **便宜**：DeepSeek 的定价比 OpenAI 低一个数量级，初学者不用心疼
2. **API 兼容**：DeepSeek 的 API 格式和 OpenAI 一模一样，换个 `base_url` 就能切成 OpenAI
3. **能力够用**：对于理解代码、运行命令、生成 patch 这类任务，DeepSeek 足够了

切成 OpenAI 只需要改一个地方——`model` 段：

```yaml
# config/default.yaml
model:
  model_name: gpt-4o
  base_url: ""                  # 去掉 base_url，走 OpenAI 默认端点
  api_key_env: OPENAI_API_KEY   # .env 里换成 OPENAI_API_KEY=你的key
```

tools 参数不用改——OpenAI 原生支持。更多覆盖方式（`-c`、环境变量、`--config`）见 [config.md](config.md)。

切成 Claude 需要换成 `anthropic` SDK，但 API 概念（messages、tools、tool_calls）完全一样。

---

## System prompt 为什么这么多规则？是不是"过度拟合"了？

最初的版本只有三句话：

```
You are a helpful assistant.
Use the bash tool to run commands in the terminal.
When your task is complete, call the submit tool.
```

迭代后变成了 ~30 行，包含角色定义、6 条 Core Rules、输出限制说明，以及一个 5 步工作流模板（Explore → Diagnose → Fix → Verify → Submit）。

**每一条规则都是踩出来的**：

| 规则 | 触发原因 |
|---|---|
| Think before you act, ONE command at a time | 模型会把多个命令拼成一行（`ls; cat; sed`），出错后无法定位 |
| Read before you edit | 模型上来就 `sed` 改文件，读都不读，经常改错位置 |
| Smallest change | 模型顺便"优化"无关代码，引入新问题 |
| Error recovery — don't blindly retry | 模型失败后重复同一个命令，死循环 |
| Verify before submit | 模型改完就提交，不跑测试，patch 是坏的 |
| Explore when stuck | 模型猜不到就停止思考，而不是继续搜集信息 |

**设计原则**：对于教学项目，system prompt 是"模型看到的第一份文档"。把它写清楚，比让模型自己摸索更高效。这个 prompt 借鉴了 SWE-agent 的 instance template（5 步工作流）、Claude Code 的行为规范（surgical changes）、以及 ReAct 论文的思考-行动交替模式。

`agent.instance_template` 把裸任务包裹进结构化的 5 步清单——这和 mini-swe-agent 在 instance template 里的做法一致，目的是给模型一个明确的 mental model。

## 为什么没做后台任务系统？

Claude Code 有 `run_in_background` + `Monitor` + `TaskOutput` 三件套：启动后台进程 → 流式推送输出 → 模型收到通知。

本项目目前只做到"教模型用 `nohup &` + `tail` 轮询"。没做完整的后台任务系统，原因：

1. **SWE agent 不需要**：修 bug 是同步流程（读代码 → 改一行 → 跑测试），3 秒一个来回。训练、构建等超长任务不在 SWE-bench 评测里
2. **输出量问题**：训练日志动辄 10 万行，直接塞进 messages 会炸上下文。需要流式推送 + 前端截断——那是整套基础设施
3. **线程安全**：主循环在读 messages，后台线程要写，需要队列 + 锁。做对了不增值（评测不考），做错了 crash
4. **模型不适应异步**：对话中间突然插一条系统通知，模型的训练数据里很少见到这种模式。很多时候模型会忽略它

**如果要做**：方案是 `bash(background=True)` → agent 内部 `threading.Thread(proc.wait)` → 结束时 `queue.put(result)` → 主循环 `_drain_background_results()` 检查 → 自动注入 user 消息。核心代码 ~20 行。讨论过但决定先不加——不是能力问题，是取舍。

## ReAct 和 function calling 的区别？

ReAct（Yao et al., 2022）的核心思想是让 LLM **交替输出推理（Thought）和行动（Action）**，两者互相增强：推理指导行动，行动获取的外部信息反过来纠正推理。

```
Thought: 我需要先看 utils.py
Action:  cat utils.py
Observation: [文件内容]
Thought: 第 15 行 return a - b，应该是 a + b
Action:  sed -i 's/a - b/a + b/' utils.py
```

本项目的 system prompt 里 "Think before you act" 就是 ReAct 的 prompt 层体现。但具体实现上，用的是 OpenAI function calling —— Thought 是模型内部隐式推理，外部只看到 Action → Observation → Action。这和经典 ReAct（Thought 是显式文字输出）有区别：

| | 经典 ReAct | 本项目 |
|---|---|---|
| 推理可见 | 显式 DISCUSSION 字段 | 隐式，模型内部推理 |
| 解析方式 | 正则提取 COMMAND | API 保证 JSON schema |
| 格式错误 | 可能发生，需要重试 | 不会发生 |
| 可调试 | 强（能看到每一步推理） | 弱（只看到命令和结果） |

选择 function calling 的核心理由：作为教学项目，用 API 原生支持的方式比手写一个脆弱的正则解析器更"干净"——读者不需要理解解析器的实现细节就能看懂 agent 循环的核心逻辑。
