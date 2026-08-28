# SWE-bench Verified 单实例报告：Sphinx 7590（失败）

## 1. 本次跑了哪个 benchmark 实例

| 项目 | 值 |
|---|---|
| benchmark | SWE-bench Verified |
| split | `test` |
| instance ID | `sphinx-doc__sphinx-7590` |
| 被测仓库 | `sphinx-doc/sphinx` |
| 模型 | `deepseek-v4-flash`（thinking disabled） |
| 实验机制 | author 首次提交后重置上下文，由 clean reviewer 独立复核 |
| 运行限制 | strict 断网容器、400 steps、2400 秒、128K context、8192 output tokens |
| 运行目录 | `runs/verified-deepseek-v4-strict-hard-clean-review/` |

**本轮只运行了 `sphinx-doc__sphinx-7590`。原计划中的 xarray 实例没有运行，不能把它记为成功或失败。**

## 2. 官方结果：失败

| instance ID | resolved | FAIL_TO_PASS | PASS_TO_PASS | infrastructure failure |
|---|---:|---:|---:|---:|
| `sphinx-doc__sphinx-7590` | **0/1（失败）** | **0/1** | 24/24 | 0 |

Agent 最终提交了 patch，但“提交了 patch”不等于“解决了实例”。SWE-bench 官方 harness 的新增失败
测试没有通过，因此本实例的最终状态是 **unresolved**。

过程开销如下：

| 阶段 | model queries | tool calls | context compression |
|---|---:|---:|---:|
| author | 90 | 90 | 0 |
| clean reviewer | 24 | 24 | 0 |
| 合计 | 114 | 114 | 0 |

## 3. 这个实例要求解决什么问题

该实例要求 Sphinx 的 C++ domain 正确处理 user-defined numeric literal（UDL），例如：

```cpp
6.62607015e-34q_J * 1q_s
```

正确实现不只要做到“能够解析”和“能够重新输出字符串”，还必须为表达式生成符合 C++ Itanium ABI
语义的 identity。也就是说，这个任务至少包含三个可观察结果：

1. parser 接受 UDL；
2. AST stringify 保留 UDL；
3. expression ID / mangling 正确表示对 literal operator 的调用。

## 4. Agent 做了什么，为什么仍然失败

候选 patch 把 UDL suffix 一起收进 `ASTNumberLiteral.data`。这使解析和 stringify 看起来正常，也没有
破坏原有的 24 个 PASS_TO_PASS 测试，但它没有实现 UDL 的调用语义。

官方新增测试给出了直接反例：

| 输入 | 官方期望 expression ID | 候选实际结果 |
|---|---|---|
| `5_udl` | `clL_Zli4_udlEL5EE` | `L5_udlE` |

候选把 `5_udl` 当成一个普通数字 literal 编码；正确结果则把它编码成对 literal operator
`operator \"\"_udl` 的调用。因此失败的直接原因不是边界输入漏测，而是 **AST 表示和 identity 建模层级错误**。

clean reviewer 做过以下检查：

- 用无 patch / 有 patch 对照确认多个 UDL 输入从解析失败变为可解析；
- 覆盖 decimal float、integer、hex、binary 和普通 literal；
- 运行 `tests/test_domain_cpp.py`，25 个已有测试通过；
- 检查 declaration stringify、signature 和若干 ID。

这些检查仍未发现错误，原因有三点：

1. 25 个已有测试没有对新的 UDL 输入断言精确 expression ID，不能充当该行为的 oracle；
2. reviewer 检查的周围 member declaration ID 不编码 initializer，因此与失败点无关；
3. parse、stringify 和 `get_id` 都来自同一个错误的 `ASTNumberLiteral` 表示，三者彼此一致不构成独立验证。

所以，本实例的核心失败是：**reviewer 验证了“补丁内部是否自洽”，却没有验证“新增语法在外部规范下
应该产生什么精确 identity”。**

## 5. Docker/harness 事故是否导致了这次失败

运行中确实发生了一个独立的 harness 问题：`edit` 两次修改约 7,288 行的 `cpp.py` 时将文件截断为
0 字节。模型随后用 `git checkout` 恢复文件，并改用脚本完成替换。

这个事故增加了调用次数和时间，但 **没有导致官方语义失败**，依据是：

- 最终候选 patch 已成功应用；
- 官方 harness 报告 `infrastructure failure = 0`；
- 24/24 个 PASS_TO_PASS 测试通过；
- 唯一 FAIL_TO_PASS 测试稳定地指出 expression ID 不符合预期。

事故根因是旧 Docker timeout wrapper 将 `setsid` 命令放到后台后，非交互 shell 把 stdin 接到
`/dev/null`；`docker exec -i` 内的 `cat > file` 先截断文件，再立即读到 EOF。该问题已经在
`972f025` 中修复，并用真实 Docker 完成约 300 KiB 文件往返和 timeout descendant cleanup 验证。

因此应分开记录两条结论：

- benchmark 失败原因：UDL identity / mangling 实现错误；
- harness 运行事故：stdin 丢失造成大文件截断，已恢复且已修复，不改变本次 benchmark 的失败判定。

## 6. 对 clean-review 机制的结论

clean reset 本身按设计工作：reviewer 的初始上下文只有 system、原始 issue、候选 patch 和审计要求，
没有继承 author 的 90 轮对话。它减少了直接沿用作者结论的风险，但没有自动产生真正独立的 oracle。

本次结果只支持以下结论：

- 分离作者和 reviewer 的默认上下文是有价值的；
- 仅重置上下文不足以保证审查质量；
- reviewer 必须为新增 construct 枚举所有可观察行为，并直接断言精确结果；
- 旧测试只有在包含新输入并断言相关输出时，才能作为新行为的 oracle；
- 同一错误 AST 派生出的多个结果不能冒充相互独立的证据。

## 7. 已经完成的修改

以下改动已经实现并提交，不是后续设想：

| commit | 状态 | 修改目的 |
|---|---|---|
| `fc73234` | 已完成 | provider 价格全为 0 时警告 cost cap 实际不可执行 |
| `d05e11a` | 已完成 | 可靠清理 Docker timeout 后的子进程 |
| `972f025` | 已完成 | 保留 Docker 写文件命令的 stdin，避免大文件被截断 |
| `6477834` | 已完成 | 提供只读、可搜索、可分页的 `trajectory` 证据工具 |
| `e98d73c` | 已完成 | 在 SWE-bench clean reviewer 中启用 trajectory，并收紧 exact-oracle 规则 |

轨迹继续采用两层存储：`.traj.json` 保存紧凑的 model-facing view，`.events.jsonl` 保存完整的
append-only journal。clean reviewer 默认不继承作者对话；需要时可用
`trajectory(query, start, events)` 检索原始命令和输出。取回的是待验证的 evidence，而不是自动可信的
conclusion。

## 8. 下一步计划（尚未执行）

1. 先配置真实的 DeepSeek token 价格或明确的 provider cost policy，使 `agent.cost_limit` 真正生效。
2. 只重跑 `sphinx-doc__sphinx-7590`，保持断网、步数、时间和上下文上限不变，避免同时改变多个变量。
3. 要求 reviewer 对 UDL 在相关 identity version 下的精确 expression ID 建立直接测试；只验证
   parse/stringify 或重跑旧测试不算通过审查。
4. 以官方 `FAIL_TO_PASS = 1/1`、`PASS_TO_PASS = 24/24` 作为成功门槛，同时记录 reviewer 是否调用
   trajectory、取回了什么证据、是否减少重复探索。
5. Sphinx 得到可解释结果后再运行 xarray。若 reviewer 仍把相关性不足的证据当 oracle，再考虑真正
   分离的 critic/author protocol 或更换 reviewer model，而不是继续堆叠提示词。

本轮没有继续运行 xarray，是因为 Sphinx 已在 114 次调用后直接否证了“clean reset + 原审计提示就足够”
这一假设，同时价格为 0 使成本上限无法约束真实费用。这是一次明确记录的 early stop，不是缺失或隐藏的
xarray 结果。

## 9. 结果与证据位置

- 运行目录：`runs/verified-deepseek-v4-strict-hard-clean-review/`
- 官方评分报告：`runs/verified-deepseek-v4-strict-hard-clean-review/reports/deepseek-v4-flash.deepseek-v4-strict-hard-clean-review-sphinx.json`
- 官方测试输出：`logs/run_evaluation/deepseek-v4-strict-hard-clean-review-sphinx/deepseek-v4-flash/sphinx-doc__sphinx-7590/test_output.txt`

**一句话结论：本轮只跑了 SWE-bench Verified 的 `sphinx-doc__sphinx-7590`，官方结果失败；失败来自
UDL expression ID 的语义建模错误，不是 Docker 事故，下一轮将用已经补强的轨迹访问和 exact-oracle
规则先复跑同一实例。**
