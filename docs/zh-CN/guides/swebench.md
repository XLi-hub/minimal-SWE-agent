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

## 磁盘紧张时的串行 wrapper

Docker/containerd 分区较小时，使用仓库里的 wrapper，并明确给出 instance id 列表。它会先
generation，再只对同一个实例运行官方 harness，完成清理后才进入下一个实例：

```bash
conda run -n minimal-SWE-agent python scripts/run_swebench_low_disk.py \
  --instances-file instances.txt \
  --subset verified --split test \
  --output runs/verified-low-disk \
  --model deepseek-flash \
  --provider https://api.deepseek.com \
  --api-key-env DEEPSEEK_API_KEY \
  --pre-pull \
  --input-price-per-1m <当前未缓存输入单价> \
  --cache-hit-price-per-1m <当前缓存命中输入单价> \
  --output-price-per-1m <当前输出单价> \
  --cost-limit 0.50
```

`instances.txt` 每行一个精确 ID；也可以重复使用 `--instance ID`，或使用含有 ID 的
`.json`/`.jsonl` 文件（记录形式包含 `instance_id`）。provider 和三个 token 单价都显式
传入，`agent.cost_limit` 才能按当前美元计费；单价应以 provider 当前价格为准，不要直接
复制旧实验的数值。cost 停止阈值按每个 generation 实例分别计算，因此
`N * cost-limit` 是累计停止阈值，不是严格账单上限；每题都可能多出触发停止的最后一次
完整模型请求费用。官方 evaluation 不调用模型 provider；实际 usage 和 cost 仍会写在每个
trajectory 中。

同一个 output 目录就是断点。wrapper 每完成一个实例就更新 `low_disk_status.json`；再次运行
时会跳过 generation、evaluation、清理都已完成的记录。用 `--retry-failed` 重试失败记录，或
用 `--redo-existing` 明确重做已有 prediction。wrapper 固定 generation 为一个 worker，并把
一个 instance id 传给官方 evaluator，因此串行循环内部不会隐藏并发评分。

为兼容原有 runner，pre-pull 默认关闭；下一批磁盘紧张运行请显式加 `--pre-pull`。当镜像
拉取可能超过核心环境的 300 秒启动/拉取超时时，wrapper 会以 `--pull-timeout`（默认 1800 秒，
可配置）逐题串行拉取精确镜像，再开始 generation。拉取失败会写入 `low_disk_status.json`，跳过该题
的 API generation 和官方 evaluation，但仍会进入 cleanup 的 `finally`；用 `--no-pre-pull`
可显式保持兼容默认行为。

每个实例结束时，`finally` 只尝试精确执行
`docker image rm <该实例的 SWE-bench 镜像>`。默认还会尝试 image GC，但先执行
`docker image ls --filter dangling=true --quiet` 预检；已有 dangling image（或预检失败）时会
警告并跳过 `docker image prune --force`。用 `--no-image-prune`/`--no-gc` 可完全关闭这个受保护
的提示。wrapper 绝不会运行 `docker system prune`。用 `--dry-run` 可只查看 generation、
evaluation 和清理命令，不启动模型、harness 或 Docker 命令。

上面的命令由 `conda run` 启动；子命令复用该环境的 `sys.executable`，脚本内部不会再嵌套
`conda`。

## 严格 profile

命令会在普通默认值上叠加
[`swebench.yaml`](../../../src/mini_agent/config/benchmarks/swebench.yaml)，其关键策略包括：

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

evaluation adapter 运行官方 harness，只统计本次调用中新建或修改的 report。目录中已有的同名
report 会保留但不会计入，因此重复使用 `--run-id` 时，本次汇总不会混入旧报告。具体 CLI 参数以
`--help` 为准：

```bash
conda run -n minimal-SWE-agent minimal-swebench-eval --help
```

## 固化运行结果

generation 和官方评分完成后，验证整轮产物并生成稳定汇总层：

```bash
conda run -n minimal-SWE-agent python scripts/finalize_swebench_run.py \
  runs/verified-20 \
  --expected-count 20
```

`--expected-count` 必须等于 prediction 数量。命令会校验 `preds.json`、`preds.jsonl`、
`statuses.json`、已提交的 trajectory、event sidecar 以及 `reports/` 下的 JSON 报告。每个实例
目录既可以是 `<instance_id>/`，也可以是 `instances/<instance_id>/`。请让 `reports/` 只保留要纳入
最终索引的证据，因为 finalizer 会读取其中全部 `reports/*.json`。默认把最早的报告记录为初始结果，
按修改时间选择最新报告作为最终结果。

命令会生成 `final/README.md`、`instances.txt`、`summary.json`、`manifest.json`、
`failures.json` 和 `checksums.sha256`。请保留完整的上层运行目录，因为 manifest 和校验和会引用
`final/` 外的文件。验证生成的文件：

```bash
cd runs/verified-20/final
sha256sum -c checksums.sha256
```

需要为某个实例指定最终报告时使用 `--report-override INSTANCE_ID=PATH`；需要附加 JSON 审计记录时
使用 `--annotations annotations.json`。

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
