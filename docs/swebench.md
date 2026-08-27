# SWE-bench

项目提供独立的 `minimal-swebench` 命令，用同一套 Agent/Model/Environment 组件生成
SWE-bench predictions。数据集依赖保持可选，普通 `minimal` 安装不会被评测依赖拖大。

`full`、`verified`、`lite`、`multimodal` 和 `multilingual` 别名指向当前
`SWE-bench/*` 官方数据集。这些数据集包含新版 harness 需要的 `image`、`eval_script`
等字段；也可以直接传完整 Hugging Face dataset id 或本地数据集路径。

## 安装与前置条件

```bash
pip install -e ".[bench]"
docker version
```

官方 SWE-bench evaluation images 通常面向 x86_64 Linux，并且首次运行需要拉取体积较大的
Docker 镜像。真实模型运行还需要配置对应的 API key；默认仍读取 `OPENAI_API_KEY`。

## 单实例调试

先用一个实例检查模型、镜像和输出格式：

```bash
minimal-swebench \
  --subset verified \
  --split test \
  --instance django__django-11099 \
  --model gpt-4o-mini \
  --output runs/smoke
```

`--instance` 也接受按 `instance_id` 排序后的数字下标，例如 `--instance 0`。

## 批量生成 predictions

```bash
minimal-swebench \
  --subset verified \
  --split test \
  --slice 0:20 \
  --workers 4 \
  --model gpt-4o-mini \
  --output runs/verified-20
```

常用选择和恢复参数：

- `--filter REGEX`：按 `instance_id` 过滤；
- `--slice START:STOP:STEP`：过滤后切片；
- `--shuffle --seed 42`：固定种子打乱；
- `--redo-existing`：重跑所有已存在结果；
- `--retry-failed`：只重跑已有但状态不是 `submitted` 的实例；
- `-c key=value`：覆盖 benchmark YAML，例如 `-c agent.cost_limit=1.5`。

每个实例都会创建独立模型、容器和 Agent，并在 `finally` 中清理容器。结果文件使用临时文件
加 `os.replace` 原子更新，因此线程 worker 不会互相覆盖 predictions。

## 输出

```text
runs/verified-20/
├── preds.json                 # keyed JSON，便于 resume
├── preds.jsonl                # 一行一个 prediction，供 harness 使用
├── statuses.json              # exit status / exception 概览
└── <instance_id>/
    └── <instance_id>.traj.json
```

每条 prediction 都包含：

```json
{
  "model_name_or_path": "gpt-4o-mini",
  "instance_id": "owner__repo-123",
  "model_patch": "diff --git ..."
}
```

runner 会验证提交看起来像 unified diff。如果模型错误地提交了文字总结，会从容器执行
`git diff --binary --no-ext-diff` 兜底；两者都没有有效 patch 时状态记为 `invalid_patch`。

## 配置

默认评测覆盖在
[`config/benchmarks/swebench.yaml`](../src/mini_agent/config/benchmarks/swebench.yaml)：

- 工作目录 `/testbed`；
- `bash -c` 配合 `BASH_ENV=/root/.bashrc`，加载镜像内 testbed 环境；
- 工具命令超时 120 秒（普通配置默认 30 秒），给跨文件修复和测试留出时间；
- 镜像拉取超时 300 秒；
- 最终必须调用 `submit` 并提交完整 unified diff；
- 对偶发的无工具响应允许两次纠正。

高难实例建议单并发运行，并使用 400 步、2400 秒的 Agent 预算。SWE-bench 配置还把
Agent 压缩预算设为 128K、为下一轮保留 8K token，并保留最近 8 轮；这些都是当前
benchmark 配置的默认值。必要时可以显式覆盖：

```bash
minimal-swebench \
  --subset verified --split test \
  --instance sympy__sympy-13878 \
  --workers 1 \
  -c agent.context_window=128000 \
  -c agent.reserve_tokens=8000 \
  -c agent.keep_last_n_turns=8 \
  -c agent.max_steps=400 \
  -c agent.max_time=2400 \
  -c tools.default_timeout=120 \
  --model gpt-4o-mini \
  --output runs/verified-hard
```

首次正式批量运行前，建议先确认模型价格配置不是默认的 0，否则 `agent.cost_limit` 只记录
上限而无法按真实美元成本触发。

## 用官方 harness 评分

评分依赖单独安装，不会混入普通生成环境：

```bash
pip install -e ".[eval]"

minimal-swebench-eval runs/verified-20/preds.jsonl \
  --dataset verified \
  --split test \
  --workers 4 \
  --run-id verified-20 \
  --report-dir runs/verified-20/reports
```

该命令以参数列表启动官方 `python -m swebench.harness.run_evaluation`，不经过 shell，
并把 dataset、predictions、worker 数、单实例 timeout 和 report directory 显式传入。
harness 和生成命令共用上述数据集别名，因此 `--dataset verified` 会解析为
`SWE-bench/SWE-bench_Verified`。
harness 完成后会定位 `<model>.<run-id>.json`，打印 resolved/total/rate，并原样返回官方
进程退出码。评分会启动 Docker、执行真实测试，可能消耗大量磁盘和时间，因此不会被默认
测试或生成命令隐式触发。

只评少量实例时可以重复传 `-i`：

```bash
minimal-swebench-eval runs/smoke/preds.jsonl \
  --dataset verified --run-id smoke \
  -i django__django-11099 -i sympy__sympy-20590
```

如果不想在本地运行 Docker harness，也可以直接把同一份 `preds.jsonl` 交给 SWE-bench
官方云端工具；认证和外部提交保持为显式用户操作，本项目不会自动上传预测结果。

## 测试边界

默认测试使用可注入的假 dataset/model/environment，不下载数据、不启动 Docker、不调用 API。
真实 SWE-bench 镜像和模型调用属于显式 E2E 操作，不会由日常测试命令触发。
