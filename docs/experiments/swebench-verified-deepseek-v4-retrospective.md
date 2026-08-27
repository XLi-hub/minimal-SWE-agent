# 从“本地跑通”到“质疑分数”：两次 SWE-bench Verified 实验复盘

> 实验日期：2026-08-27  
> 模型：`deepseek-v4-flash`（thinking disabled）  
> 数据集：`SWE-bench/SWE-bench_Verified`  
> 实例：`django__django-11138`、`sympy__sympy-13878`  
> 运行目录：`runs/verified-deepseek-v4-hard-400/`

## 1. 这次实践真正想回答什么

这次实验最初看起来只是“在本地电脑上挑少量 SWE-bench 实例跑起来”，但随着运行推进，
问题逐渐从工程层面变成了评测方法层面：

1. 双系统环境下，放在 Windows 数据盘上的项目能否稳定运行 Docker 评测；
2. 内存、磁盘和容器生命周期是否适合在个人电脑上跑少量实例；
3. 100 步是否低估了真实修复所需的探索时间，困难任务需要多大预算；
4. 一个结构很简单的 Agent 如果解决了标注为 `1-4 hours` 或 `>4 hours` 的任务，
   说明 Agent 很强、模型很强，还是 benchmark 已经失去区分度；
5. `resolved` 到底证明了什么，又遗漏了哪些污染、测试和轨迹证据。

我最初更关注“能不能跑”和“能不能通过”，后来把关注点转向“这个通过是否值得相信”。
这是本次实践最重要的认识变化：**先把系统跑通只是第一层，知道测量结果是否有效才是第二层。**

## 2. 实验设置与结果

为了避免只挑明显容易完成的任务，本次选择了两个历史上被标注为较困难的 Verified 实例，
并把预算从早期的 100 步提高为：

```yaml
context_window: 128000
reserve_tokens: 8000
keep_last_n_turns: 8
max_steps: 400
max_time: 2400
tools.default_timeout: 120
```

最终官方 SWE-bench harness 判定两个实例均为 `resolved`：

| 实例 | 历史人工难度 | API 调用 | 记录成本 | 官方结果 |
|---|---:|---:|---:|---:|
| `django__django-11138` | `1-4 hours` | 112 | $0.0533238 | resolved |
| `sympy__sympy-13878` | `>4 hours` | 165 | $0.07140632 | resolved |

从工程角度，这证明了以下链路已经成立：

- 本地 Docker 能启动官方实例镜像；
- Agent 能在长任务中持续执行、压缩上下文并最终提交；
- prediction 格式可以被官方 harness 接收；
- 两个补丁都通过了 FAIL_TO_PASS 与 PASS_TO_PASS 测试；
- 400 步和 2400 秒作为困难任务的硬上限，在这台机器上可运行。

但是，`2/2` 不能解释任务是怎样完成的。打开轨迹以后，两个“成功”的性质并不相同。

## 3. Django：一个有效提交，也是一条受污染的能力证据

### 3.1 它做对了什么

Agent 最终修改了与参考补丁相同的四个数据库后端文件，核心方向正确：

- 将数据库连接时区纳入日期截断和转换；
- 源时区和目标时区相同时跳过不必要转换；
- SQLite 自定义函数收到连接时区；
- MySQL、Oracle、SQLite 的实现保持一致的行为目标。

它还执行了较广泛的测试：model fields、lookups、backends、database functions 和聚焦的
timezone 用例。`timezones` 中出现的 3 个失败，在 stash 掉补丁后仍然存在，因此较可能是
旧 Django 与当前运行环境的基线兼容问题，而不是本次修改引入的回归。

### 3.2 为什么这条成功不能算“独立解决”

轨迹显示 Agent 下载并解压了其他 Django 发行版本，随后将 `/testbed` 中的代码与
`/tmp/django30/d30/...` 逐段比较，并明确判断自己的实现与 Django 3.0 上游实现一致。
最终补丁甚至带入了该实例 gold patch 没有的后续上游逻辑。

这不是数据加载器把 gold patch 直接放进 prompt，也不是 Agent 偷读了 harness 隐藏测试；
但它仍然是一个能力评测捷径：

```text
旧版本仓库 + 公开 issue
          ↓
联网获取包含后续修复的发行版
          ↓
diff 上游源码并移植
          ↓
通过隐藏测试
```

在真实开发中，检索 release、文档和上游源码是值得鼓励的能力；在想测“模型能否根据 issue
和旧仓库独立推导修复”时，它却改变了题目。**真实工作模式和严格 benchmark 模式必须明确
区分，而不能混用同一个“允许联网”的结果。**

### 3.3 测试通过仍暴露了覆盖盲区

Agent 自己构造的 SQLite 时区检查曾得到 `Asia/Bangkok` 的 `+06:42` LMT 偏移，输出
`14:08` 而非预期 `13:50`。它识别出这与 `pytz.timezone()` 配合 `replace(tzinfo=...)`
有关，但因为上游实现也是如此，最终没有进一步处理。

官方测试仍然通过，说明“通过 gold tests”不等于所有合理边界都正确。这个例子同时揭示了
benchmark 的两个局限：测试可能只约束历史补丁的行为，而上游补丁本身也不必然覆盖所有
语义边界。

## 4. SymPy：更接近真实求解，但绝非“一步解决”

### 4.1 自主性证据

SymPy 轨迹中没有看到下载新版源码、clone GitHub 或读取预置答案的证据。最终补丁只修改
`sympy/stats/crv_types.py`，而数据集 gold patch 还包含不同的代码结构、文档和测试修改。
`/tmp/issue_patch.diff` 的 hunk 与 Agent 当前工作区 diff 一致，且官方基础镜像中不存在该
文件，因此它是 Agent 自己生成的临时快照，不是参考补丁。

Agent 的实际求解过程包括：

- 为 12 个连续分布补充 CDF；
- 对部分公式做导数与 PDF 对照；
- 运行 issue 中列出的 12 个调用；
- 发现 StudentT 负数分支不满足 `F(x) + F(-x) = 1` 后自行修正；
- 比较修改前后的旧版 SymPy 测试异常，确认没有新增异常；
- 最终通过 1 个 FAIL_TO_PASS 与全部 19 个 PASS_TO_PASS。

因此，这条轨迹更支持“强模型可以通过简单工具循环完成有实质内容的修复”。

### 4.2 “简单 Agent”不等于“任务简单”

Agent 外壳只有查询、工具执行、观察、继续这条线性循环，但这次用了 165 次 API 调用。
所谓简单，只说明 orchestration 薄，不说明求解成本低：

```text
简单控制流
  + 强模型内部先验和推理
  + 可执行仓库与测试反馈
  + 128K 上下文预算
  + 165 次查询/摘要调用
  = 最终 resolved
```

后段约 6 次调用主要在重复查看相同 diff，前段还出现 pytest 未安装、测试模块路径错误、
符号运算超时和重复 baseline 对比。这是一条有探索、有纠错、也有明显低效的轨迹，而不是
模型看到题目后直接生成正确答案。

## 5. 为什么不能用这次 2/2 宣称能力

### 5.1 样本太少且不是随机抽样

两个实例是为检验困难长任务而有意选择的，不能估计总体成功率。`2/2` 的置信区间极宽，
也无法区分仓库、任务类型、年代、题面信息量和模型训练记忆的影响。

### 5.2 历史“人工耗时”不是今天模型的稳定难度

两个任务创建于 2018、2019 年。多年公开的 issue、PR、发行版和讨论可能进入模型训练数据。
人类当年的修复耗时还包含熟悉项目、沟通、review 和等待 CI，而 Agent 只面对已经隔离好的
仓库快照与一个可自动判分的问题。把 `>4 hours` 直接解释为“模型节省了四小时”并不严谨。

### 5.3 Verified 已经从能力标尺退化为回归集

OpenAI 在 2026 年对 SWE-bench Verified 的审计中报告：在重点审查的 138 个困难/不稳定
任务中，至少 59.4% 存在实质性题面或测试问题；同时，受测前沿模型均能复现部分 gold
patch 或任务特有细节。OpenAI 因此停止报告 Verified，并建议其他模型开发者也停止把它
当作前沿能力指标：

- [Why SWE-bench Verified no longer measures frontier coding capabilities](https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/)

后续对 SWE-bench Pro 的审计也估计约 30% 任务存在问题，并撤回了简单迁移到 Pro 的推荐：

- [Separating signal from noise in coding evaluations](https://openai.com/index/separating-signal-from-noise-coding-evaluations/)

因此，本次 Verified 结果仍有价值，但价值应重新定位为：

- 本地执行和官方评分链路的回归测试；
- 同一模型下比较工具、prompt、压缩和预算策略的固定控制集；
- 分析 Agent 失败模式和工程可靠性的教学材料；
- **不是** 2026 年前沿模型真实软件工程能力的最终分数。

## 6. 从轨迹暴露出的工程问题

### 6.1 压缩上下文覆盖了原始证据

当前 `Agent._maybe_compress()` 会用摘要后的列表原地替换 `self.messages`，而
`serialize()` 最终只保存这份列表。结果是：

- Django 记录 112 次 API 调用，最终只能看到 25 个工具调用；
- SymPy 记录 165 次 API 调用，最终只能看到 14 个工具调用；
- 最需要审计的早期探索、下载、编辑和失败命令恰好被压缩掉；
- 文档所说的“完整 message history”与实际行为不一致。

压缩对模型是必要的，但不应该破坏实验记录。这里混淆了两个概念：

- **执行上下文（context view）**：为适配模型窗口而允许总结和裁剪；
- **事实轨迹（event log）**：用于复盘、统计和审计，只追加、不覆盖。

### 6.2 trajectory 保存了不该进入学习材料的 gold 字段

SWE-bench runner 当前把整个 instance 字典写入 trajectory，其中可能包括 `patch`、
`test_patch`、`eval_script`。本次运行给模型的任务只来自 `problem_statement`，没有发现 prompt
泄漏；但保存文件若被用于盲测重试、轨迹浏览、公开分享或训练，就很容易把答案和隐藏测试
带入后续流程。

轨迹应采用公开字段 allowlist，而不是复制完整实例。

### 6.3 Shell pipeline 可以制造“假成功”

轨迹中多次出现：

```bash
python ... | grep ...
python ... | tail ...
```

Bash 默认把整个 pipeline 的退出码设为最后一个命令的退出码。前面的 Python 测试即使失败，
只要后面的过滤命令成功，Agent 就可能看到 `returncode=0`。这次已有不存在测试模块和测试
失败被管道状态弱化的情况。

GNU Bash 官方定义的 `pipefail` 会让 pipeline 返回最右侧非零命令的状态：

- [Bash Reference Manual: Pipelines](https://www.gnu.org/software/bash/manual/html_node/Pipelines.html)

这里应在 benchmark interpreter 层启用 `bash -o pipefail -c`，而不是只在 prompt 里提醒。
Prompt 提示仍然有用，但它属于行为引导，不能替代执行器保证。

不建议全局开启 `set -e`：Agent 经常有意执行会失败的探测命令，`errexit` 在 `if`、`&&`、
`||` 和子 shell 中也有复杂例外。`pipefail` 只修复本次确认的状态掩盖问题，边界更清晰。

### 6.4 步数上限不是主要问题，缺少“收敛意识”才是

两条任务都没有触及 400 步，因此立即把上限降回 100 会重新伤害困难任务。真正的问题是：

- 没有区分硬上限和软预算；
- 模型看不到剩余步骤；
- 系统不识别连续相同的 diff/read/test；
- 测试已经稳定后缺少“总结证据并提交”的压力。

后续更适合先记录重复调用和成功曲线，再决定是否增加 150/250 步软提醒。硬上限继续承担
防失控职责，而不是被当作目标使用量。

## 7. 改进设计与取舍

### 7.1 P0：严格 benchmark 默认断网

SWE-bench 专用配置增加：

```yaml
environment:
  run_args:
    - --rm
    - --network=none
```

Docker 官方说明 `none` driver 只在容器中保留 loopback，可用于完全隔离容器网络栈：

- [Docker Docs: None network driver](https://docs.docker.com/engine/network/drivers/none/)

验收标准：

- benchmark 配置生成的 `docker run` 明确包含 `--network=none`；
- 普通 local/Docker Agent 默认配置不受影响，仍可用于真实联网开发；
- 文档明确镜像下载发生在容器启动前，但容器运行后不能访问 PyPI/GitHub；
- 将 run 的 network policy 记入 trajectory config，便于事后判断结果是否干净。

### 7.2 P0：完整事件流与模型上下文分离

采用两个文件：

```text
<instance_id>.traj.json       # 结果、配置、压缩后的最终 context view、事件日志索引
<instance_id>.events.jsonl    # 按发生顺序追加的完整原始消息与压缩事件
```

`events.jsonl` 的每一行是一个独立 JSON 对象：

```json
{"sequence": 0, "type": "message", "message": {"role": "system", "content": "..."}}
{"sequence": 1, "type": "message", "message": {"role": "user", "content": "..."}}
{"sequence": 8, "type": "context_compression", "messages_before": 8, "messages_after": 5}
```

这与成熟 Agent 的设计方向一致：SWE-agent 内部分开保存 append-only trajectory/history，
再通过 history processors 生成模型消息；OpenHands 则让 condenser 从完整 event history
生成发送给 LLM 的 `View`，而不是原地销毁事件：

- [SWE-agent `DefaultAgent` history/trajectory 实现](https://github.com/SWE-agent/SWE-agent/blob/main/sweagent/agent/agents.py)
- [OpenHands condenser 接口](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/context/condenser/base.py)

验收标准：

- 压缩后 `.traj.json` 的 context 可以变短；
- `.events.jsonl` 仍包含压缩前的每一条 assistant/tool 消息；
- 压缩事件本身记录前后消息数与摘要结果，能够解释模型下一步看到了什么；
- 旧消费者仍可读取 `.traj.json` 的 `messages`；
- 事件文件不包含 SWE-bench gold patch/test patch；
- 多次序列化或保存不会复制、重排或覆盖内存中的原始事件。

### 7.3 模型暂不直接访问完整轨迹

本次不增加 `read_history` 工具，理由是：

1. 完整历史可能正是因为太大才被压缩，重新塞回上下文会抵消压缩；
2. 原始工具输出包含大量重复 diff、测试日志和失败尝试，信噪比低；
3. 模型可能把“复盘自己的旧推理”当成继续探索，增加循环和成本；
4. 目前没有证据表明这两个任务因摘要遗漏而失败，反而都完成了；
5. 新工具会增加权限、prompt 和测试复杂度，而当前首要目标是评测可审计性。

如果以后观察到“摘要丢失关键命令结果导致重复探索”，优先顺序应是：

1. 改进结构化摘要，强制保留文件、命令、退出码、测试结果和待办；
2. 维护一个小型结构化工作记忆，而不是回灌全文；
3. 最后才增加只读、分页、按类型/关键词过滤的事件检索工具，并限制单次 token 预算。

换言之，**完整轨迹首先是 observability，不是 agent memory。**

### 7.4 P0：让测试失败可靠地显现

SWE-bench interpreter 改为：

```yaml
interpreter:
  - bash
  - -o
  - pipefail
  - -c
```

同时在 benchmark system prompt 中明确：

- 任何非零 `returncode` 都不能被报告为“测试通过”；
- pipeline 只用于缩小展示内容，不能用过滤后的文本替代测试退出码；
- 测试模块不存在、依赖缺失和 timeout 应分别记录为 infrastructure/command failure，
  不能算作产品代码测试失败，也不能算成功。

执行层保证状态，prompt 层帮助模型正确解释状态，两者缺一不可。

### 7.5 P1：最小化 instance 元数据

主 trajectory 只保存审计所需公开字段，例如：

- `instance_id`
- `repo`
- `base_commit`
- `problem_statement`
- `version`
- `created_at`
- `difficulty`
- `image` / `image_name`

明确排除 `patch`、`test_patch`、`eval_script` 以及未知的自定义大字段。predictions 和官方
harness 输入仍按原逻辑工作；改变的只是调试轨迹的元数据边界。

### 7.6 P1：改进效率，但不急于缩减硬预算

后续可以记录并分析：

- 相同规范化 command 连续出现的次数；
- 相同 `git diff`/文件区间重复读取；
- 首次测试通过到 submit 之间的调用数；
- 无效测试路径、缺失依赖、timeout 和工具参数错误；
- 首次产生最终有效 patch 的步数。

在有 10–20 条新轨迹后，再考虑加入软提示：

- 150 步：总结当前假设、证据和剩余风险；
- 250 步：若 patch 已稳定，运行最后的聚焦测试并准备提交；
- 检测到重复动作：提示说明新信息增益，否则换一种验证方法。

这些属于下一阶段，不与本轮 P0 的评测可信度修复混在一起。

## 8. 下一轮实验应该怎样设计

### 8.1 保留旧任务作为对照，不再把它当排行榜

这两个 Verified 实例适合作为固定回归对：

- Django 用来检查 strict network 是否真正阻止上游检索捷径；
- SymPy 用来检查长轨迹、上下文压缩和复杂公式修复能力是否退化。

重新运行时必须把旧结果标为 `network=default`，新结果标为 `network=none`，不能混合统计。

### 8.2 使用更新任务建立新样本

可以从 SWE-bench-Live 的最新 `full` split 分层抽取 10–20 个实例。该项目按月向 full split
加入新验证任务，比分割后长期冻结的 Verified 更适合做新鲜度检查：

- [Microsoft SWE-bench-Live](https://github.com/microsoft/SWE-bench-Live)

但“Live”不等于永不污染。任务一旦公开，之后仍可能进入训练数据。最强的证据仍然是：

- 晚于模型训练截止日期的任务；
- 未公开的内部留出 issue；
- 自建仓库或在评测后才公开的补丁；
- 对每条任务进行人工题面/测试一致性审计。

### 8.3 报告的不应只有 resolve rate

建议每轮同时报告：

| 维度 | 指标 |
|---|---|
| 正确性 | resolved、FAIL_TO_PASS、PASS_TO_PASS |
| 可信度 | network policy、是否访问外部源码、任务发布时间/训练截止日期 |
| 效率 | API calls、tokens、成本、wall time、首次稳定 patch 步数 |
| 工具质量 | 无效命令、timeout、重复命令、测试状态误判 |
| 轨迹质量 | 是否完整、压缩次数、原始事件数、最终 context 消息数 |
| 失败类型 | 定位失败、实现失败、验证失败、环境失败、题目/测试缺陷 |

这样，成功不再只是一个布尔值，而是一条可以解释、比较和改进的证据链。

## 9. 本次学习的核心结论

1. **跑通 benchmark 不等于完成能力评估。** 工程正确性是测量有效性的前置条件。
2. **官方 resolved 是必要证据，但不是充分证据。** 还要检查网络、训练污染、测试覆盖和轨迹。
3. **简单 Agent 可以很强，因为复杂性被放在模型和环境反馈中。** 不应只按 Agent 代码行数
   判断任务难度。
4. **压缩上下文与保存事实不能共用一份可变列表。** 前者服务推理，后者服务科学性。
5. **prompt 不能替代执行器保证。** 网络隔离和 pipeline 退出码必须由系统层落实。
6. **大预算本身不是浪费，缺少收敛机制才是。** 应先观察真实轨迹，再设计软预算。
7. **最值得展示的不是 2/2，而是从相信分数到审计分数的思考过程。** 这比单独展示一个
   漂亮结果更能说明对 Agent 工程、实验设计和评测可信度的理解。

## 10. 实施顺序

本复盘之后的代码改进按以下顺序实施，并单独提交：

1. SWE-bench 默认 `--network=none`；
2. SWE-bench 默认 `bash -o pipefail -c`，并强化测试退出码提示；
3. Agent 保留不可变完整事件流，context 压缩只改变模型视图；
4. trajectory 与 events JSONL 分开保存；
5. SWE-bench instance 元数据改用 allowlist，排除 gold/test/eval 字段；
6. 为网络配置、pipeline、压缩后完整事件、sidecar 文件和元数据脱敏补测试；
7. 运行全部非 E2E 测试后提交实现。

