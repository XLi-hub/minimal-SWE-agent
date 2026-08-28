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
    ├── <instance_id>.traj.json       # 结果、配置和最终模型 context view
    └── <instance_id>.events.jsonl    # 完整、只追加的原始消息/压缩事件
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

`.traj.json` 中的 `messages` 是 Agent 结束时实际使用的 context view，发生过上下文压缩时
可能只包含 system、原始任务、摘要和最近若干轮。完整的 assistant/tool 交换不会再被压缩
覆盖，而是按顺序写入 `.events.jsonl`；压缩本身也是一个事件，记录压缩前后消息数和生成的
摘要，并保存压缩后的精确 context snapshot。事件日志用于审计和复盘，默认不会作为工具
开放给模型。

为避免轨迹本身成为泄题载体，runner 只保存公开任务和环境字段，不会把数据集中的 gold
`patch`、`test_patch`、`eval_script`、隐藏测试列表或未知自定义字段复制到轨迹。

## 配置

默认评测覆盖在
[`config/benchmarks/swebench.yaml`](../src/mini_agent/config/benchmarks/swebench.yaml)：

- 工作目录 `/testbed`；
- 严格评测容器使用 `--network=none`，镜像仍可由宿主在启动前拉取，但运行中的 Agent
  不能访问 PyPI、GitHub 或其他上游源码；
- 同一配置启用 `environment.block_network_commands`：常见下载、远程 Git 和包管理器
  联网命令会在进入 shell 前得到明确的 policy error。这个检查用于减少无效尝试和提供
  可解释反馈；Docker 的网络命名空间仍是处理间接或混淆访问的安全边界；
- `bash -o pipefail -c` 配合 `BASH_ENV=/root/.bashrc`，既加载镜像内 testbed 环境，
  又避免 `pytest | tail` 一类命令把前序测试失败伪装成成功；
- 工具命令超时 120 秒（普通配置默认 30 秒），给跨文件修复和测试留出时间；
- 镜像拉取超时 300 秒；
- 第一次 `submit` 只捕获候选 diff，不结束运行；随后 Agent 会收到一次不包含 hidden test
  结果的证据审计；review 从原始 issue 和候选 patch 的干净 context 开始，避免继续被作者阶段
  的既有解释锚定，完整作者轨迹仍保存在 `.events.jsonl`；
- 审计检查底层契约、状态 API 的参数/生命周期行为矩阵，以及 identity、serialization、
  mangling 等行为在仓库内的独立类比或 canonical oracle；同一个新表示派生出的多个方法彼此
  一致，只能证明内部自洽，不能证明外部契约正确；
- 完成审计后第二次 `submit` 才是最终提交，且必须包含完整 unified diff；
- 对偶发的无工具响应允许两次纠正。

这道 review gate 解决的是生成侧的过早收敛，不改变官方评分边界。它不会运行或返回 hidden
tests，也不会把第一次候选的 evaluator 结果反馈给模型，因此标准运行仍可与正常 SWE-bench
结果比较。若要把官方失败信息交还模型继续修，那属于独立的 repair mode，必须使用不同 run
ID 并标记为 non-comparable，不能混入一次通过率。

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
上限而无法按真实美元成本触发。`minimal-swebench` 会在“上限大于 0、三个单价全为 0”时
输出启动告警；它不会猜测随供应商和模型变化的价格。

断网是本项目 strict SWE-bench profile 的评测策略，而不是普通 Agent 的全局策略。真实开发
若需要查文档、下载依赖或比较上游实现，应使用普通配置，并在报告中明确标记 network
policy；联网与断网结果不应混在同一成功率中。

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
