# Clean-context review 续试：去锚定不是独立验证

> 前一轮：[SWE-bench 高难双实例审计复跑](swebench-verified-hard-audit-rerun.md)  
> 本轮输出：`runs/verified-deepseek-v4-strict-hard-clean-review/`  
> 模型：`deepseek-v4-flash`（thinking disabled）  
> 实例：`sphinx-doc__sphinx-7590`

这轮只改变生成侧机制：第一次 `submit` 后把 model-facing context 重置为原始 issue、审计清单
和候选 patch。完整作者过程仍写入 append-only events，但不默认交给 reviewer。运行继续使用 strict
断网容器、400 steps、2400 秒、128K context、8192 output tokens，并由官方 harness 独立评分。

## 结果：过程分开了，错误抽象没有改变

| 指标 | author | clean reviewer | 合计/官方结果 |
|---|---:|---:|---:|
| model queries | 90 | 24 | 114 |
| tool calls | 90 | 24 | 114 |
| context compression | 0 | 0 | 0 |
| FAIL_TO_PASS | — | — | 0/1 |
| PASS_TO_PASS | — | — | 24/24 |
| infrastructure failure | — | — | 0 |

官方新增用例仍要求 `5_udl` 的 expression ID 是
`clL_Zli4_udlEL5EE`，候选仍生成 `L5_udlE`，因此 resolved 为 0/1。评分结束后已删除精确的
4.05 GiB SWE-bench image；没有遗留容器。

clean reset 的工程语义是有效的：reviewer 收到的初始 context 只有 system、原始任务和带候选
patch 的审计消息，作者的 90 轮历史没有混入。它也确实重新做了以下工作：

- 用 `git stash` 证明多个 UDL 输入在无 patch 时失败、应用 patch 后可解析；
- 覆盖 decimal float、integer、hex、binary 和普通 literal 对照；
- 重跑 `tests/test_domain_cpp.py`，25 个公开测试通过；
- 检查 declaration stringify、signature 和若干 ID。

但这些动作仍没有回答关键问题：新产生的 UDL AST 在每个 ID version 下应当输出什么。reviewer
把“不含 UDL 输入的旧 literal golden tests 全过”称为 independent oracle，又用 member declaration
的 ID 作旁证；该 ID 根本不编码 initializer，所以无法证明 expression ID。它最后把同一个
`ASTNumberLiteral.data` 派生出的 parse、stringify 和 `get_id` 再次当成相互验证。

这说明去锚定只解决“reviewer 是否继承作者结论”，不解决“模型是否会把不相关证据错误归类为
oracle”。提示词可以扩大搜索，却不能自动保证证据的可采纳性。

## 为什么没有立刻继续跑 xarray

这轮 Sphinx 已经直接命中新增机制的目标域：parser、AST、identity 和 Itanium mangling。它仍在
114 次调用后犯下与前轮相同的契约错误，足以否证“仅靠 clean reset + 原审计提示即可解决”这
个假设。与此同时三项价格仍为 0，`agent.cost_limit=3` 不能执行真实美元上限。

因此本轮在 Sphinx 后停止，而不是机械跑完 xarray：先修已知 harness 缺口，再开始下一组可比较
实验。这里的停止不是把失败藏起来，而是顺序实验的 early-stop 规则——一个针对性样本已经否证
机制时，不继续为同一版本购买第二份证据。

## 轨迹设计的新结论：clean 默认，证据按需

完全恢复作者上下文会重新引入 anchoring；完全隐藏又会浪费作者已经付费得到的原始命令和输出。
本轮 author 曾探索 `g++` literal operator mangling，但 clean reviewer 不知道这条线索，只能从头
搜索，最后选错了 oracle。

后续采用中间方案：

1. `.traj.json` 继续保存紧凑 model-facing view，`.events.jsonl` 保存完整 append-only journal；
2. clean reviewer 默认不继承作者对话；
3. SWE-bench profile 提供只读 `trajectory(query, start, events)`，由 reviewer 在需要时搜索精确
   命令/输出，返回内容分页且受字符预算限制；
4. 取回的是 evidence，不是被信任的 conclusion。reviewer 仍必须独立计算期望 observable。

这分别由 `6477834`（通用工具和 Agent 接线）与 `e98d73c`（SWE-bench 启用和审计规则）实现。
新规则还明确：旧测试只有在实际输入包含新 construct、并断言其精确输出时，才可作为该行为的
oracle；parser/AST 修改必须枚举 stringify、各 identity version 和 rendering 等所有新可达
observable。周围对象恰好通过的 ID 不算证据。

## 运行中发现的 harness 回归

第一次使用 `edit` 修改 7288 行的 `cpp.py` 时，文件两次变成 0 字节。模型用 `git checkout`
恢复并改用脚本替换，最终提交的 patch 没有被截断，但浪费了大量调用，也说明这轮不能只按模型
质量解释。

根因不是 read 分页或字符串替换，而是前一版 Docker timeout wrapper：它为 `setsid` 把命令放到
后台；非交互 POSIX shell 会在没有显式重定向时把后台 stdin 接到 `/dev/null`。于是
`docker exec -i` 内的 `cat > file` 先截断目标，再立即读到 EOF。`972f025` 显式保留原 stdin，
真实 Docker 已验证约 300 KiB 文件完整往返，同时 timed-out descendant marker 测试仍通过。

这个事故强化了一个评测原则：trajectory 中的低质量行为可能来自 model、tool、environment 或
evaluator 四层，必须先分层归因；只看最终 0/1 会把 harness regression 错算成模型能力。

## 下一次可比较实验的门槛

下一次复跑不再只问“review 有没有多跑测试”，而要记录：

- reviewer 是否调用 `trajectory`，检索了什么证据，是否因此减少重复探索；
- 每个新 construct 的 exact observable 是否在官方评分前被直接断言；
- oracle 是否真的覆盖新输入，而非只覆盖相邻旧行为；
- author/reviewer 各自 queries、工具调用、压缩和真实 provider 费用；
- 大文件 edit 往返与 Docker timeout cleanup 是否保持通过。

如果这些过程条件满足而 resolved 仍没有改善，下一步应考虑真正分离的 critic/author protocol 或
不同 reviewer model，而不是继续叠加自然语言清单。当前证据只支持“按需历史比自动恢复更合理”，
还不支持宣称它已经提高 SWE-bench 成功率。
