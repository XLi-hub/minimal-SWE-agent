# SWE-bench Verified 高难双实例失败复盘：为什么“自测全绿”仍是 0/2

> 运行目录：`runs/verified-deepseek-v4-strict-hard/`  
> 模型：`deepseek-v4-flash`（thinking disabled）  
> 实例：`pydata__xarray-6992`、`sphinx-doc__sphinx-7590`  
> 官方 harness：0/2 resolved，0 个 infrastructure failure

这次实验刻意选择了 Verified 中标注为 `>4 hours`、此前没有跑过的两个实例。它们都
到达了 `submitted`，也都通过了原仓库里的相关测试，但官方 harness 最终判定全部失败。
这不是一次“模型没工作”的实验，而是一次更有价值的反例：**Agent 做了很多正确动作，
却没有建立足够强的证据证明补丁满足新行为。**

## 结果不是“提交成功”

| 实例 | Agent API calls | 完整事件 | 上下文压缩 | FAIL_TO_PASS | PASS_TO_PASS | resolved |
|---|---:|---:|---:|---:|---:|---|
| `pydata__xarray-6992` | 60 | 123 | 0 | 0/12 | 945/945 | 否 |
| `sphinx-doc__sphinx-7590` | 124 | 249 | 1 | 0/1 | 24/24 | 否 |

`submitted` 只表示 Agent 调用了 `submit` 并产出了可应用的 patch。它不说明新增行为正确，
更不能替代官方 harness。今后的状态解释必须区分：

1. `submitted`：有候选 patch；
2. `completed`：harness 成功运行；
3. `resolved`：FAIL_TO_PASS 和 PASS_TO_PASS 都满足要求。

## xarray：修掉了异常，却没有恢复数据模型不变量

### Agent 做对了什么

- 复现了 `DataVariables.__len__()` 返回负数的问题；
- 找到了 `_coord_names` 可能包含 `_variables` 中不存在名称这一直接原因；
- 修改 `__len__`，改为实际计数非坐标变量；
- 在 `reset_index()` 中从 `coord_names` 移除 `drop_variables`；
- 跑了大范围 xarray 测试，没有破坏 945 个 PASS_TO_PASS。

这些动作足以让 issue 中的最小示例不再抛出 `ValueError`，也足以让旧测试保持绿色。因此
Agent 形成了“根因已解决”的判断。

### 真正缺失的部分

负长度不是独立 bug，而是索引重构后状态不一致的一个可见症状。正确行为还必须同时维护：

- `_variables` 与 `_coord_names` 的成员关系；
- `_indexes` 与 MultiIndex level coordinates 的对应关系；
- `set_index()` 替换旧索引时哪些坐标要转成 base variable；
- `reset_index(drop=True)` 是否真正删除维度变量和 level variables；
- MultiIndex 剩一个 level 时是否按兼容语义重命名回维度；
- dimensions 是否需要通过 `_replace_with_new_dims()` 重新计算。

Agent 修的是读取无效状态时的消费者 `DataVariables.__len__`，但没有完整修复产生无效状态的
`set_index/reset_index` 生命周期。官方新增的 12 个测试全部失败，恰好覆盖这些状态转换。

### 机制教训

“最小改动”不等于“最靠近 traceback 的改动”。当异常揭示内部不变量已被破坏时，应先问：

> 这个状态本来允许存在吗？如果不允许，谁产生了它？

只有确认无效状态是合法中间态时，消费者侧的防御性修复才足够。否则它可能只是把错误藏起来。

## Sphinx：解析成功不等于 AST 契约完整

### Agent 做对了什么

- 识别了 `identifier_re` 开头的 word boundary 无法匹配数字后 UDL suffix；
- 支持 number/string/character user-defined literals；
- 复现并修复了实现过程中的无限循环；
- 工具 120 秒超时后成功恢复，没有中止整个实例；
- 原有 `tests/test_domain_cpp.py` 25 项全部通过；
- issue 中的 `6.62607015e-34q_J * 1q_s` 能够完成 parse 和 stringify。

### 真正缺失的部分

Sphinx 的 C++ AST literal 不只承担“能解析、能转回字符串”两个职责。它还参与：

- `get_id(version)` 的 C++ symbol ID 生成；
- literal operator 的 ABI 风格 name mangling；
- signature rendering；
- UDL suffix 对 `operator""suffix` 的 identifier/xref 表达；
- 标准整数/浮点 suffix 与 UDL suffix 的边界区分。

Agent 自己设计的验证期待 `5_udl` 对应类似 `L5_udlE` 的 ID。官方测试要求把 UDL 表达成
literal operator 调用，例如 `clL_Zli...E...E`。因此 parse 和 stringify 虽然正确，唯一的
FAIL_TO_PASS 仍然失败。

轨迹中 Agent 其实意识到 ABI 可能不同，但随后用自己猜测的 ID 写了临时测试，再用这个
测试证明自己的实现正确。这是典型的循环论证：**测试验证了实现者的假设，而不是仓库既有
抽象的契约。**

## 上下文压缩是次要因素，不是统一解释

xarray 没有发生压缩，仍然失败，所以不能把 0/2 简单归因于 summary 丢信息。

Sphinx 发生了一次压缩。完整 `.events.jsonl` 保留 249 个事件，模型视图压缩为较短历史。
摘要保留了文件、命令和测试结果，但也把“所有相关测试已通过”和“ID 可能与 grading 不同”
一起压缩成了当前状态。后续模型更多是在确认已有方案，而不是重新打开未验证假设。

因此压缩机制要保留的不只是“做过什么”，还应明确区分：

- 已观察证据；
- 当前假设；
- 已否定方案；
- 尚未验证的契约；
- 测试覆盖了什么、没有覆盖什么；
- 提交前仍需关闭的风险。

完整轨迹和压缩视图继续并存是正确的。默认不需要让模型读取整个 event journal；先提高摘要
的证据结构更简单、更可控。只有实验显示摘要仍持续丢失关键证据时，再考虑分页、限额、只读
的历史检索工具。

## harness 应该改什么，不应该改什么

### 不应该：把隐藏测试反馈给标准评测中的 Agent

官方 test patch 在提交前不可见，是 SWE-bench 的核心评测边界。若第一次失败后把具体
FAIL_TO_PASS traceback 发回模型让它修第二次，再把第二次记作标准成绩，就等于把测试答案
变成训练信号，结果不能和普通 SWE-bench run 比较。

可以提供研发用 `repair mode`，但必须满足：

- 使用新的 run id；
- 报告中标记 `harness_feedback=true`；
- 不计入标准 resolved rate；
- 保留每轮 patch 和测试反馈，不能覆盖第一次失败。

### 应该：强化提交前可验证证据

harness/runner 可以在不泄漏隐藏测试的前提下做这些事情：

1. **候选 patch 预检**：非空、能应用、`git diff --check` 通过、没有修改禁止路径；
2. **状态语言分离**：Runner 只称 candidate/submitted，harness 才能写 resolved；
3. **提交前契约审计**：要求 Agent 再检查一次受影响对象的全部公开契约，而不只是最小复现；
4. **测试证据清单**：记录精确命令、returncode，以及这些测试覆盖/未覆盖的行为；
5. **严格运行防护**：断网、管道 `pipefail`、进程树超时清理、原始事件 append-only；
6. **基础设施分类**：镜像拉取失败、命令超时、测试失败必须分开，不能混成 unresolved。

## 已实施的基础改进

- `5c8358e`：严格 SWE-bench 在 tool 分发前阻止常见联网命令，同时保留
  `--network=none` 作为安全边界；
- `d3c56fa`：修复 runner compact factory 参数重复/遗漏绑定；
- `ca78a76`：摘要调用后重新检查时间与费用，malformed summary response 也保留 usage；
- `7fcecfe`：宿主机超时时同步清理容器内 exec 进程组，Windows 清理后代进程树；
- 完整事件 sidecar、压缩模型视图和官方 harness report 继续分别保存。

这些改进提高运行可信度，但不会自动让错误 patch 变正确。下一层需要直接针对“验证策略”改进。

## 下一层机制设计

### 1. 证据导向摘要

把 summary 从“进度回顾”改成“可继续推理的状态”：必须保留 invariants/contracts、证据、
未验证假设、测试覆盖空白和下一步关闭条件。

### 2. 提交前二阶段审计

第一次 `submit` 只登记 draft。Agent 获得一次明确的审计机会：

- 这是根因修复还是症状屏蔽？
- 哪些相关状态会被创建、转换、删除？
- 受影响对象除 parse/execute 外还有哪些 ID、serialization、rendering 或 lifecycle 契约？
- 新行为是否有独立于实现细节的本地 regression check？
- 旧测试通过能证明什么，不能证明什么？

完成审计后第二次 `submit` 才终止。这个机制不能保证成功，但会把“我觉得好了”转换成一组
必须关闭的证据问题。

### 3. 可比较的实验设计

后续重新运行同两个实例时，应固定：

- 同一 base image、模型和 thinking 设置；
- 同一网络策略；
- 同一 max steps/time；
- 只改变一个机制（例如先只改 summary，再只加 submit audit）；
- 同时报告 resolved rate、API calls、wall time、压缩次数和 contract-audit 行为。

否则即使从 0/2 变成 1/2，也无法判断是哪项改进产生作用。

## 最重要的学习

这次失败说明，复杂 SWE-bench 实例的难点不是“能不能写代码”，而是：

> 能否从有限 issue 描述中恢复仓库维护者真正保护的抽象边界，并设计不依赖自己实现假设的
> 验证证据。

旧测试全绿、最小复现不报错、diff 很小，都只是证据的一部分。高质量 Agent 还需要主动
寻找被遗漏的契约，以及明确承认哪些部分仍未验证。
