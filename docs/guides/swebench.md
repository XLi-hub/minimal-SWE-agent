# SWE-bench 指南

SWE-bench workflow 分为 generation 与官方 evaluation 两阶段。先用单实例确认环境和费用，
再扩大 slice 与 workers；提交成功不等于官方 resolved。

## 安装

数据集 generation 使用可选依赖：

```bash
conda run -n minimal-SWE-agent pip install -e ".[bench]"
```

官方评分再安装独立 eval extra：

```bash
conda run -n minimal-SWE-agent pip install -e ".[eval]"
```

不要把依赖装进 base conda 环境。运行前还需要 Docker daemon、足够磁盘，以及目标 provider
的 API key。

## 单实例冒烟

```bash
conda run -n minimal-SWE-agent minimal-swebench \
  --subset verified --split test \
  --instance 0 \
  --model gpt-4o-mini \
  --output runs/verified-smoke
```

`--instance` 可以是精确 instance id，也可以是按 id 排序后的数字索引。先检查：容器成功
启动、工作目录是 `/testbed`、轨迹/事件已保存、prediction 是 unified diff、资源已清理。

默认 token 单价为零时，CLI 会警告 cost limit 无法按美元生效。正式批量前通过配置文件或
`-c cost...` 设置当前 provider 的真实单价。

## 选择任务

内置 alias 包括 `full`、`verified`、`lite`、`multimodal`、`multilingual`。可组合：

```bash
minimal-swebench \
  --subset verified --split test \
  --filter 'django__django-' \
  --slice 0:20 \
  --output runs/django-20
```

`--shuffle --seed 42` 在 slice 前进行确定性 shuffle。记录 alias、实际 dataset、split、filter、
slice、seed 和 instance ids，实验才可复现。

## 并发 generation

```bash
conda run -n minimal-SWE-agent minimal-swebench \
  --subset verified --split test \
  --slice 0:20 --workers 4 \
  --model gpt-4o-mini \
  --output runs/verified-20
```

每个 worker 会建立自己的模型、Docker 容器、Agent 和轨迹。增加并发前评估 API rate limit、
费用、Docker CPU/内存、镜像存储和磁盘写入压力。

## 严格 profile

命令会在普通默认值上叠加
[`swebench.yaml`](../../src/mini_agent/config/benchmarks/swebench.yaml)，其关键策略包括：

- Docker 工作目录 `/testbed`；
- 容器 `--network=none`；
- 常见网络命令预执行拦截；
- Bash `-o pipefail`；
- benchmark 专属预算和 tool timeout；
- trajectory 工具；
- clean-context draft review 与 evidence checkpoint。

该 profile 不接收隐藏测试或 evaluator 反馈。reviewer 只能使用 issue、仓库、当前可用测试、
候选 patch 和运行自己的事件记录。

## 输出目录

```text
runs/verified-20/
├── preds.json
├── preds.jsonl
├── statuses.json
└── <instance_id>/
    ├── <instance_id>.traj.json
    └── <instance_id>.events.jsonl
```

`preds.jsonl` 是官方 harness 输入。trajectory 的 instance metadata 刻意排除 gold patch、
隐藏测试和 eval script；不要从原始 dataset row 手工补回这些字段。

## 断点续跑

同一输出目录默认保留已有终局结果。按需要选择：

```bash
minimal-swebench ... --retry-failed
minimal-swebench ... --redo-existing
```

`--retry-failed` 面向失败项，`--redo-existing` 明确重做已有项。重跑前备份需要保留的实验
产物，并记录代码 commit 与配置；同名目录里混入不同版本会削弱可比性。

## 官方评分

```bash
conda run -n minimal-SWE-agent minimal-swebench-eval \
  runs/verified-20/preds.jsonl \
  --dataset verified --split test --workers 4 \
  --run-id verified-20 \
  --report-dir runs/verified-20/reports
```

evaluation adapter 运行官方 harness 并收集 report。具体 CLI 参数以 `--help` 为准：

```bash
conda run -n minimal-SWE-agent minimal-swebench-eval --help
```

## 如何判读结果

至少分开报告：

1. generation 是否 `submitted`，是否有非空 unified diff；
2. Agent 自己运行了哪些测试、精确 return code 与覆盖范围；
3. container/image/setup 是否正常；
4. 官方 harness 是否成功完成；
5. 最终 resolved 与否；
6. token/API calls/cost、steps 和墙钟；
7. review 是否改变 patch 或新增独立验证。

“本地测试绿”“提交了 diff”“harness resolved”是三种不同证据。失败时先区分基础设施、
测试环境、patch 格式和行为契约，再归因于模型。

## 测试边界

runner 与 dataset/storage 的单元测试不需要真实 API 或 Docker；官方 E2E generation 可能
产生费用，不属于默认测试。仓库完整非 E2E 命令见[测试指南](testing.md)。

架构细节见[Benchmark 层](../architecture/benchmark-layer.md)，历史复盘见
[实验索引](../experiments/index.md)。
