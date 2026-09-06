# 实验索引

这里保存有明确时间与样本背景的 SWE-bench 实验。它们解释当时观察到的失败和设计变化，
不充当当前 API、默认值或排行榜声明。阅读时应对照当前[架构](../architecture/overview.md)与
[SWE-bench 指南](../guides/swebench.md)。

## 推荐顺序

1. [Verified 两实例回顾](swebench-verified-deepseek-v4-retrospective.md)：从表面 2/2 结果中
   识别污染、轨迹和 pipeline 风险；
2. [高难双实例 0/2 分析](swebench-verified-hard-0-of-2-analysis.md)：为什么自测绿色仍未恢复
   隐含数据模型与 AST 契约；
3. [审计复跑](swebench-verified-hard-audit-rerun.md)：draft audit 改善了过程，但没有把结果
   变成成功；
4. [Clean-review 续试](swebench-clean-review-followup.md)：去锚定、轨迹回查与 harness 事故
   如何分层归因。

## 共同主题

- `submitted` 只说明 Agent 接受了一个结果；
- 本地测试通过只覆盖实际运行的输入与 assertion；
- 官方 harness resolved 是独立结果，也可能受基础设施影响；
- assistant 的自我评价不是证据；
- 完整 events、return code、patch 和报告应共同保存；
- 少量、挑选或已公开污染的样本不能支持宽泛能力结论。

## 当前机制映射

| 实验问题 | 当前代码位置 | 说明 |
|---|---|---|
| 压缩覆盖原始历史 | `context.py` + `persistence.py` | messages/events 双视图 |
| 作者结论污染 review | `evidence.py` | 机器事实 checkpoint |
| clean review 丢失探索 | `trajectory` tool | 有界、按需回查 |
| pipeline 假成功 | SWE-bench YAML interpreter | Bash pipefail |
| 外部检索污染 | Docker run args + network policy | network none 是实际边界 |
| gold 字段进入轨迹 | benchmark metadata allowlist | 只保留公开字段 |
| runner 过于集中 | `benchmarks/_swebench/` | dataset/storage 下沉 |

## 如何引用实验

报告结论时至少附上：代码版本、配置 profile、模型/provider、实例 id、generation status、
完整 patch、精确测试命令与 return code、harness 版本和最终报告。若缺一项，应明确称为限制。

实验记录中的模型能力、provider 上下文窗口、价格或外部 benchmark 状态可能已经变化；
当前运行配置以 YAML 和 provider 官方信息为准。

## 报告模板

新增实验记录时，建议包含以下最小结构：

```text
问题：这次只改变哪个变量，想证伪什么？
设置：commit、profile diff、provider/model、instance selection、seed
Generation：exit status、steps、API calls、cost、wall time
证据：命令、return code、测试输入与 assertion、最终 patch
Evaluation：harness command/version、基础设施状态、resolved report
结论：观察支持什么，不支持什么
下一步：保持哪些变量不变，下一轮只改什么
```

不要只粘贴模型自述或末尾几行测试输出。对 pipeline 命令记录 `pipefail` 状态；对超时记录
是否终止进程树；对 review 记录作者阶段、checkpoint 摘要与 reviewer 阶段各自调用量。

## 可比性检查

- 实例集合、split 或排序变化时，不直接比较 resolve rate；
- prompt、模型、预算和 review 同时变化时，只能称为系统级对比；
- 重跑同一公开任务会有记忆或数据污染风险；
- harness 事故必须与 patch 语义失败分开；
- “旧测试通过”只能证明执行到的旧契约，不能覆盖新增行为；
- 同一实现派生出的 stringify/hash/render 互相一致，不算独立 oracle；
- 结果为 0/2 或 2/2 的小样本都不支持总体能力估计。

## 与正式文档的边界

实验提出并已实现的机制，应在 architecture/reference 页面以当前代码语义重写；未实现想法
留在报告中，不写成默认能力。若报告内的计划后来改变，保留原文并从索引链接当前设计，
不要回写历史让实验显得事后必然。

返回[文档首页](../index.md)。
