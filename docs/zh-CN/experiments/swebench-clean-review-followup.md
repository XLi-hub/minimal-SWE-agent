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

该实例要求 Sphinx 正确理解 C++ 的 user-defined literal（UDL）。以 `5_udl` 为例，它看起来像一个
带后缀的数字，但在 C++ 中表达的是一次调用：

```cpp
operator ""_udl(5)
```

上面的写法是为了说明语义，不是原始源码的等价改写。Sphinx 需要把 `5_udl` 解析成“用参数 `5` 调用
literal operator `_udl`”，并为这个 C++ 表达式生成稳定的内部 ID。

因此任务不是只让 parser 接受这段文本，而是同时做到：

1. 能读入 `5_udl`；
2. 能重新显示为 `5_udl`；
3. 内部语义必须是“调用 `_udl` operator”，不能把整个 `5_udl` 当成一个普通数字。

## 4. Agent 做了什么，为什么仍然失败

**一句话原因：Agent 只修好了“读入这段文字”，没有修好“理解这段文字”。**

候选 patch 直接把 `_udl` 粘到数字内容后面，把 `5_udl` 作为一个整体存进普通数字节点。这样做产生了
下面的结果：

| 检查层次 | 正确行为 | 候选 patch | 结果 |
|---|---|---|---|
| 读取源码 | 接受 `5_udl` | 可以接受 | 通过 |
| 重新显示 | 输出 `5_udl` | 可以原样输出 | 通过 |
| 理解语义 | `_udl` operator 接受参数 `5` | 把 `5_udl` 当成一个普通数字 | **错误** |
| 生成内部 ID | 生成“operator 调用”的 ID | 生成“数字 literal”的 ID | **错误** |

这就像把一条指令原样抄写出来，却没有识别出它是一次函数调用。表面文本正确，内部表示仍然错误。

官方新增测试正好检查了这个内部语义：

| 输入 | 官方期望 expression ID | 候选实际结果 |
|---|---|---|
| `5_udl` | `clL_Zli4_udlEL5EE`（operator 调用） | `L5_udlE`（普通数字） |

两串 ID 本身不需要记。它们只是证明：官方要求的是“调用 `_udl` operator”，候选提交的却是“一个叫
`5_udl` 的数字”。因此新增测试没有通过，最终 `FAIL_TO_PASS = 0/1`，整个实例判定为失败。

clean reviewer 做过以下检查：

- 用无 patch / 有 patch 对照确认多个 UDL 输入从解析失败变为可解析；
- 覆盖 decimal float、integer、hex、binary 和普通 literal；
- 运行 `tests/test_domain_cpp.py`，25 个已有测试通过；
- 检查 declaration stringify、signature 和若干 ID。

reviewer 没发现这个问题，是因为它主要检查了前两层——“能不能读”和“能不能原样输出”——没有直接
检查第三层“它在语义上是不是一次 operator 调用”。具体来说：

1. 25 个已有测试没有对新的 UDL 输入断言精确 expression ID，不能充当该行为的 oracle；
2. reviewer 检查的周围 member declaration ID 不编码 initializer，因此与失败点无关；
3. parse、stringify 和 `get_id` 都来自同一个错误的 `ASTNumberLiteral` 表示，三者彼此一致不构成独立验证。

所以，本实例的核心失败是：**实现把 UDL 当成特殊数字，而 C++ 要求把它当成 operator 调用；reviewer
又没有为这个调用语义建立直接测试。**

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
| `7cd2cf7` | 已完成 | 建立不含作者结论和 candidate body 的机器证据 checkpoint |
| `b5ec908` | 已完成 | clean review 同时接收机器索引与不可信压缩工作记忆；摘要失败可降级 |
| `b5691c5` | 已完成 | trajectory 支持按事件、角色、工具和 return code 组合过滤 |

轨迹继续采用两层存储：`.traj.json` 保存紧凑的 model-facing view，`.events.jsonl` 保存完整的
append-only journal。clean reviewer 默认不继承作者全文，而是接收一份有界的机器证据索引和明确
标为不可信的模型摘要；需要时可用 `trajectory(query, start, events, event_type, role, tool_name,
returncode)` 检索原始命令和输出。所有过滤条件按 AND 组合。取回的是待验证的 evidence，而不是
自动可信的 conclusion。

## 8. 下一步计划（尚未执行）

1. 先配置真实的 DeepSeek token 价格或明确的 provider cost policy，使 `agent.cost_limit` 真正生效；
   checkpoint 至多多一次摘要请求，这次调用也必须纳入真实费用。
2. 只重跑 `sphinx-doc__sphinx-7590`，保持断网、步数、时间和上下文上限不变，把新的 memory
   handoff 作为唯一主要变量。
3. 要求 reviewer 对 UDL 在相关 identity version 下的精确 expression ID 建立直接测试；只验证
   parse/stringify 或重跑旧测试不算通过审查。
4. 以官方 `FAIL_TO_PASS = 1/1`、`PASS_TO_PASS = 24/24` 作为成功门槛，同时记录 reviewer 是否调用
   trajectory、使用了哪些结构化过滤、取回了什么证据、是否减少重复探索。
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

导航：[实验索引](index.md) · [SWE-bench 指南](../guides/swebench.md) ·
[轨迹格式](../reference/trajectory-format.md)
