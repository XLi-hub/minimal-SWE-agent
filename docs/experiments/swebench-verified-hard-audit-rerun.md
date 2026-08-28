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
