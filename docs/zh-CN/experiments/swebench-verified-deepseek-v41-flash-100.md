# DeepSeek V4.1 Flash 的 100 题 SWE-bench Verified 实验

> 实验日期：2026-09-12 至 2026-09-14
>
> 模型：通过 `deepseek-flash` API 别名调用 DeepSeek V4.1 Flash，关闭 thinking
>
> 系统：本项目增强版 mini-agent harness，不是原版 mini-SWE-agent
>
> 数据集：`SWE-bench/SWE-bench_Verified`，`test` split
>
> 生成代码提交：`676c1290c479d77bea05407104a236721a1eb2c7`
>
> 精确题目集合：`runs/verified-deepseek-v41-flash-sequential-20260912/final/instances.txt`

## 问题与范围

这次实验主要检验：在磁盘空间有限的个人工作站上，本地 harness 能否完成一轮中等规模的
SWE-bench 生成、官方评测、断点续跑、异常审计和证据保存；同时建立一个供后续系统对比使用
的固定 100 题控制集。

题目通过 seed 42 的分批列表逐步扩展到 100，最后一批记录在
`next-56-to-100-seed42.txt`。它不是 Verified 全部 500 题，也不应被包装成独立复现的排行榜
分数。真正权威的样本定义是 `final/instances.txt` 中的 100 个精确 ID，而不是口头抽样描述。

## 配置

全部题目使用 400 步、2400 秒的生成硬上限，128K 上下文窗口、8192 token 单次输出上限、
clean-context submission review、只追加的完整事件日志、Bash `pipefail`，且生成容器默认断网。

实验记录的 API 单价为：未缓存输入 $0.15/M token、cache hit 输入 $0.003/M、输出 $0.60/M。
最早 4 个校准题使用每题 $0.50 的成本停止阈值，之后 96 题使用 $0.20。它们是停止阈值，
不是预付预算，也不代表当前供应商报价。

运行环境为 Python 3.10.20、`swebench` 5.0.2、`datasets` 5.0.1、`openai` 2.50.0、
Docker client/server 29.7.2 和 Linux 6.8.0-136。

## 结果

| 口径 | Resolved | Unresolved | Error | Infrastructure | Ambiguous 标记 |
|---|---:|---:|---:|---:|---:|
| 首轮官方报告 | 75 | 18 | 7 | 0 | 4 |
| 审计后的最终选择 | 75 | 19 | 6 | 0 | 3 |

解决率为 **75.0%**，Wilson 95% 区间为 **65.70%–82.45%**。调整后分数没有提高，只是对一个
首轮超时项获得了更强证据，并重新归类。

`sphinx-doc__sphinx-7985` 首轮触发 harness 的 1800 秒测试超时。随后复用完全相同的模型补丁，
不调用模型，以 6000 秒上限重评。测试在 1981.69 秒完成，结果为 `unresolved`，共有 3 个
linkcheck 测试失败。首轮口径保留原始 timeout；审计口径显式选择完成后的报告。

## 失败与重试审计

以下 6 个 `error` 是模型输出的补丁格式不合法，官方 harness 无法应用：

- `astropy__astropy-13453`
- `django__django-11163`
- `django__django-14792`
- `django__django-15957`
- `django__django-16263`
- `scikit-learn__scikit-learn-14894`

另有 3 个 unresolved 报告带 `no_tests_collected` ambiguous 标记：
`pylint-dev__pylint-4551`、`pytest-dev__pytest-7205`、`sphinx-doc__sphinx-8595`。
它们仍计为模型/系统失败；ambiguous 只是附加标记，不会将其移出分母。

`django__django-15128` 第一次生成达到时间上限且没有可用补丁。覆盖之前保存了完整旧轨迹，
再生成后该题 resolved。第一次尝试使用 209 次 API 调用、成本 $0.15184442；替代尝试使用
147 次调用、成本 $0.08264339。

15 个 Docker 镜像曾在模型调用前因 EOF 或 short read 拉取失败，之后全部成功重试，所以它们
属于已恢复的基础设施事故，不是最终 benchmark 失败。精确 ID 和证据保存在
`final/annotations.json` 与评测日志归档中。

## 成本

| 成本口径 | 美元 | API 调用 |
|---|---:|---:|
| 最终 100 条轨迹 | $3.10541145 | 7,461 |
| 被替换但保留的首次尝试 | $0.15184442 | 209 |
| 实验实际总账单 | **$3.25725587** | **7,670** |

平均每题 $0.03257256，平均每个最终 resolved 题 $0.04343008。Docker 官方评测和重评不调用
模型，因此不会增加模型费用。

## 产物布局与验证

运行目录保存原始证据，`final/` 作为稳定汇总层。顶层 `README.md` 是统一入口，100 个单题目录
全部集中在 `instances/`，不再散落在运行目录根部：

- `instances/`：100 个目录，每题各含一份 trajectory 和一份完整事件日志；
- `summary.json`：首轮/最终计数、Wilson 区间和成本；
- `manifest.json`：每题轨迹、事件日志、全部评测尝试、最终选择和生成重试；
- `failures.json`：最终 25 个非 resolved 题及其报告证据；
- `annotations.json`：人工复核的重试与失败归因；
- `raw-evaluation-logs.tar.zst`：102 个评测日志目录，包含重试；
- `checksums.sha256`：所有被引用原始文件和最终文件的哈希。

进入 final 目录后可验证全部保留文件：

```bash
sha256sum -c checksums.sha256
```

以下命令可以确定性重建汇总：

```bash
conda run -n minimal-SWE-agent python scripts/finalize_swebench_run.py \
  runs/verified-deepseek-v41-flash-sequential-20260912 \
  --expected-count 100 \
  --annotations final/annotations.json \
  --report-override \
  sphinx-doc__sphinx-7985=reports/deepseek-flash.dsv41f-final-sphinx-7985-timeout6000.json
```

finalizer 会在写汇总前拒绝：JSON/JSONL prediction 不一致、非 submitted 状态、缺失或数量不匹配
的事件日志、没有分类报告的题目，以及未知的 override。

## 如何理解这次结果

作为工程作品，这次实验有较强价值：它在 100 题上展示了可断点续跑执行、低磁盘 Docker
镜像生命周期管理、重试归因、cache-aware 成本统计、完整轨迹、官方 harness 接入和可复验
产物管理。这些内容足以成为简历项目的核心证据。

但 75% 对“模型通用能力”的证明较弱：Verified 是公开的历史 benchmark；这里只覆盖分批选择
的五分之一；harness 还加入了 clean review 等自定义机制。后续比较应固定这 100 个 ID 和全部
系统配置；如果改变模型、prompt、review、预算或样本，应明确称为另一组系统级实验。
