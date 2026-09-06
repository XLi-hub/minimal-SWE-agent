# SWE-bench 高难双实例审计复跑：过程更好，结果仍是 0/2

> 对照运行：`runs/verified-deepseek-v4-strict-hard/`  
> 审计复跑：`runs/verified-deepseek-v4-strict-hard-audit/`  
> 模型：`deepseek-v4-flash`（thinking disabled）  
> 实例：`pydata__xarray-6992`、`sphinx-doc__sphinx-7590`

这次复跑固定了模型、prepared image、断网策略、`max_tokens=8192`、400 steps、2400 秒、
128K Agent context 和测试超时。相对上一轮只引入证据导向摘要与第一次 submit 后的同上下文
draft audit。两个实例顺序生成、分别评分，评分后立即删除大型 Docker image。

## 结果

| 实例 | 旧 API calls | 新 API calls | 旧/新压缩 | FAIL_TO_PASS | PASS_TO_PASS | resolved |
|---|---:|---:|---:|---:|---:|---|
| `pydata__xarray-6992` | 60 | 41 | 0 / 0 | 0/12 | 945/945 | 否 |
| `sphinx-doc__sphinx-7590` | 124 | 73 | 1 / 0 | 0/1 | 24/24 | 否 |

两次官方 harness 都是 0 个 infrastructure failure。API calls 下降、Sphinx 不再触发压缩，
说明过程成本和轨迹长度有所改善；但这里只各有一个样本，不能把下降归因于单一机制，更不能
用它代替 resolved rate。价格配置仍为零，因此 trajectory 中的 `$0` 只表示项目没有配置
provider 单价，不表示真实调用免费。

## draft audit 实际改变了什么

### xarray

第一次 draft 后，Agent 重新追踪了 `set_index → reset_index`，检查 MultiIndex dimension、
level drop 和普通 index，运行了 2500 多项相关测试，并确认一个额外 Pint failure 在撤销 patch
后仍存在。这些动作比上一轮更扎实。

但它仍把问题收缩成两个局部条件：

- `DataVariables.__len__` 与 `__iter__` 一致；
- dropped name 不再残留在 `_coord_names`。

官方新增用例要求的是完整 reset-index 状态机：dimension/level、single/list 参数、`drop`
True/False、MultiIndex 降级、IndexVariable 转 base Variable、重命名、维度重算以及 groupby
下游行为。Agent 验证了几个自己选择的例子，却没有先从 public 参数和邻近测试构造行为矩阵。
所以 12 个 FAIL_TO_PASS 仍全部失败。

### Sphinx

第一次 draft 后，Agent 主动检查了 construction、stringify、`get_id()` 和 signature，且用
`git stash` 证明临时回归脚本在无 patch 时会失败。这已经不是“只跑旧测试”。

关键错误是把以下结果称为 contract 证据：

```text
data='1q_s'  str='1q_s'  id2='L1q_sE'  signature='1q_s'
```

这些值全部从新塞进 `ASTNumberLiteral.data` 的同一个字符串派生，只证明内部自洽。仓库文件
开头已声明 ID 使用 Itanium C++ ABI mangling，且已有 `ASTOperatorLiteral` 和 call expression
是可用的独立类比。官方期望 `5_udl` 的新 ID 为 `clL_Zli4_udlEL5EE`，实际仍是
`L5_udlE`。Agent 看到了 ID，却没有先从既有 literal-operator 调用抽象计算期望值。

## 新发现的 tool 问题

`read(path=...)` 虽然有 20K character cap，却没有应用 `tools.default_max_lines=100`。Sphinx
轨迹中同一个 7000 多行文件至少四次返回 20K characters、约 392 个编号行；xarray 也有一次
20K/511 行输出。模型甚至在轨迹里指出 read offset 异常，但只能改用 `sed` 绕过。

这不是失败的唯一原因，不过它会重复污染上下文、增加 anchoring。`d952498` 已让 read 默认
分页，并提供 `line_start`/`lines` 连续读取，保留原始行号和字符上限。

## 为什么同上下文自审仍会失败

draft audit 给原作者增加了一次检查机会，但仍保留其完整推理历史。模型已经投入大量步骤建立
某个方案后，审计很容易变成“寻找更多证据证明当前 patch”，而不是从零尝试推翻它。两条轨迹
都出现了这种 confirmation bias：验证范围明显扩大，最终抽象选择却几乎没有变化。

因此后续机制不是简单增加第三次 submit，而是：

1. `662459e`：review 可从 system、原始 issue、candidate patch 和审计清单组成的干净 context
   开始；完整作者轨迹继续保存在 append-only events，不再作为 reviewer 的默认锚点。
2. `95aef11`：SWE-bench 启用 clean review；状态 API 必须构造行为矩阵，identity/serialization/
   mangling 必须寻找仓库内独立 oracle。由同一个新表示派生的多个方法不能互相作证。
3. 标准 run 仍不提供 hidden test。若使用上面的官方失败明细继续修复，必须另建 repair run，
   标记 `harness_feedback=true` 和 non-comparable。

## 对参数设置的结论

这两次失败都不是 step/time 不足。新一轮分别在 41、73 次 API 调用后主动提交，远低于 400
steps；没有因 2400 秒上限退出。继续把上限从 400 提高只会扩大最坏费用，不会自动发现缺失
契约。当前更合理的方向是把预算看成安全上限，把额外调用花在独立 review、对照 oracle 和
行为矩阵，而不是允许原路径无限延长。

## 下一次实验如何解释

下一次用 clean-context review 复跑时，至少要同时记录：

- resolved/F2P/P2P，而不是只看 submitted；
- reviewer 是否真的找到了独立 repository oracle；
- 是否在看官方结果前构造了覆盖 public 参数轴的行为矩阵；
- author 与 reviewer 各自 API calls、工具输出字符量和压缩次数；
- 实际 provider 费用（只有配置了单价后 trajectory cost 才可信）。

即使下一轮仍为 0/2，只要 clean reviewer 能明确指出当前 patch 的 ABI 或状态矩阵缺口，机制
也比“更多步骤”更接近可解释、可迭代的 Agent 设计；但最终是否有效仍以官方 resolved 为准。

## 后续实现：压缩记忆与按需回查并存

后续 Sphinx clean-review 单实例复跑仍然是 `0/1`；xarray 没有在那一轮复跑，不能把它写成
第二次失败。Sphinx 证明“清空作者上下文”本身并不能保证 reviewer 找到正确 ABI oracle：它
去除了锚定，也同时丢掉了作者已经付费获得的命令、测试和文件定位。于是问题不该被简化为
“保留全文”与“完全清空”二选一。

现在采用三层内存：

```text
热上下文：system + 原始 issue + 有界 checkpoint + candidate + review prompt
                              │
             reviewer 需要精确证据时主动查询
                              ▼
冷存储：append-only .events.jsonl（完整、只读、不自动回灌）
```

checkpoint 本身又分两层：

- 机器层只抽取 event sequence、工具名、命令、return code、文件路径和错误；它不复制作者
  的自然语言结论，也不复制 submit 中的大 patch；
- 模型层用 evidence-focused prompt 压缩工作状态，并明确标为不可信 navigation aid；它帮助
  reviewer 知道“可能要去哪里查”，但不能替代 repository oracle。

reviewer 仍可用 `trajectory` 读取原始记录，不过读取变成主动、局部、可组合的操作：除了
`query/start/events`，还可以按 `event_type`、`role`、`tool_name` 和 `returncode` 过滤。例如先
找 `tool_name=bash, returncode=1`，再用事件序号回查相关上下文，不需要恢复作者全文。

对应实现分成三笔提交，避免把存储、交接和检索耦合在一个大改动里：

| commit | 作用 | 不声称解决什么 |
|---|---|---|
| `7cd2cf7` | 从原始 events 建立确定性、有界、可关联 call/result 的证据 checkpoint | 不判断 patch 正确性 |
| `b5ec908` | 在 clean-review 边界强制生成 checkpoint，并保留不可信压缩摘要 | 不保证 reviewer 会找到正确 oracle |
| `b5691c5` | 为 trajectory 增加结构化组合筛选 | 不自动选择该查哪条证据 |

这个机制针对的是两次失败中的**信息组织问题**，不是直接修复任务本身：

| benchmark 实例 | 官方结果 | 根本失败 | 新机制可能改善 | 新机制不能替代 |
|---|---:|---|---|---|
| `pydata__xarray-6992` | FAIL_TO_PASS 0/12，失败 | 没有从 public 参数和状态转换构造完整 behavior matrix | checkpoint 保留已跑模式，减少 reviewer 重复探索；trajectory 可找已有失败/成功对照 | reviewer 仍必须主动枚举 dimension、level、drop、MultiIndex 等轴 |
| `sphinx-doc__sphinx-7590` | FAIL_TO_PASS 0/1，失败 | 把 UDL 建模成普通 number，而非 literal-operator call，导致 ABI ID 错误 | checkpoint 暴露 parse/stringify/ID 检查范围；reviewer 可精确回查命令与 return code | reviewer 仍必须找到 `ASTOperatorLiteral`/call expression 这一独立 oracle，并断言 exact ID |

因此不能说这三笔改动已经解决前两次 benchmark 失败。更准确的假设是：它们减少 clean reset
造成的信息损耗，同时不恢复完整推理带来的锚定；是否提高 resolved rate 必须由同配置复跑
验证。

## 下一轮对照实验（尚未执行）

下一轮应保持模型、prepared image、断网、400 steps、2400 秒、128K context 和测试超时不变，
只把 memory handoff 作为实验变量。报告必须按实例分开，不再用一个笼统的“bench 跑了”概括：

1. 先跑 `sphinx-doc__sphinx-7590`。成功门槛是 FAIL_TO_PASS 1/1、PASS_TO_PASS 24/24；同时记录
   reviewer 是否在提交前建立 exact expression-ID oracle。
2. 再跑 `pydata__xarray-6992`。成功门槛是 FAIL_TO_PASS 12/12、PASS_TO_PASS 945/945；同时记录
   reviewer 是否在看官方反馈前形成完整 behavior matrix。
3. 每个实例分别报告 author 主循环 calls、checkpoint 摘要 calls、reviewer calls、trajectory
   调用与过滤条件、压缩次数、墙钟时间和真实费用。
4. 如果仍失败，先判断是 checkpoint 丢证据、reviewer 没有主动检索，还是检索后仍选择了错误
   抽象；这三类失败对应不同改法，不能统一归咎于步数不够。

这轮不同时调整模型、prompt 大段内容和硬预算，否则即便成功也无法知道是哪项改变起作用。
400 steps 继续作为防失控的上限，不是鼓励模型用满；checkpoint 的摘要请求计入调用、成本和
墙钟，但不计入主循环 step。
