# DeepSeek V4.1 Flash：SWE-bench Verified 100 题最终结果

> 实验日期：2026-09-12 至 2026-09-14
>
> 模型：DeepSeek V4.1 Flash（API 名称 `deepseek-flash`，关闭 thinking）
>
> 系统：本项目增强版 mini-agent harness
>
> 数据集：`SWE-bench/SWE-bench_Verified`，`test` split

## 最终结论

本次一共评测 **100 题**：

| 最终结果 | 题数 | 占比 |
|---|---:|---:|
| 成功（`resolved`） | **75** | **75%** |
| 失败（非 `resolved`） | **25** | **25%** |
| 合计 | **100** | **100%** |

这里的“成功”是指模型补丁能够应用，并通过该题全部官方 FAIL_TO_PASS 和 PASS_TO_PASS 测试。
SWE-bench 不给部分分：只要还有一个规定测试失败，该题最终就是失败。

最终 25 个失败可以进一步分成：

| 失败原因 | 题数 | 说明 |
|---|---:|---|
| 功能不完整或引入回归 | **17** | 补丁应用成功，但官方测试仍有失败 |
| 测试无法正常执行 | **2** | 一题测试收集 ImportError；一题与官方测试补丁冲突 |
| 提交的 diff 不合法 | **6** | diff 截断或 hunk 格式错误，官方 harness 无法应用 |
| 合计 | **25** | 最终没有基础设施失败 |

## 25 个失败题及原因

### 1. 补丁能够评测，但官方测试失败：17 题

| 实例 | 失败证据 | 结论 |
|---|---|---|
| `astropy__astropy-14369` | 1 个 FAIL_TO_PASS、2 个 PASS_TO_PASS 失败 | 新行为未完全修复，并破坏旧行为 |
| `django__django-10973` | `test_nopass` 失败 | PostgreSQL dbshell 参数处理仍不正确 |
| `django__django-11239` | `test_ssl_certificate` 失败 | SSL certificate 命令参数未满足预期 |
| `django__django-11477` | 2 个 URL pattern 测试失败 | 可选参数和路径开头变量仍处理错误 |
| `django__django-12193` | `test_get_context_does_not_mutate_attrs` 失败 | widget context 仍会产生错误的状态变化 |
| `django__django-15252` | 2 个 migration schema 测试失败 | `MIGRATE=False` 时的测试数据库建表行为不正确 |
| `django__django-15916` | `test_custom_callback_in_meta` 失败 | ModelForm 自定义 callback 传播不完整 |
| `matplotlib__matplotlib-22871` | 1 个 PASS_TO_PASS 失败 | 修复引入日期 formatter 回归 |
| `matplotlib__matplotlib-25332` | `test_complete[png]` 失败 | figure pickle/PNG 场景仍未修复 |
| `pydata__xarray-3993` | 2 个 `test_integrate` 失败 | Dataset integration 两种模式均未满足预期 |
| `pydata__xarray-6938` | `test_to_index_variable_copy` 失败 | IndexVariable copy 语义仍不正确 |
| `pytest-dev__pytest-7205` | 9 个 FAIL_TO_PASS 失败 | 带参数 fixture 的 setup 输出格式仍不正确 |
| `sphinx-doc__sphinx-7985` | 2 个 FAIL_TO_PASS、1 个 PASS_TO_PASS 失败 | local link 检查不完整且产生回归 |
| `sympy__sympy-11618` | `test_issue_11617` 失败 | issue 对应数学行为未修复 |
| `sympy__sympy-13852` | `test_polylog_values` 失败 | polylog 特殊值仍不正确 |
| `sympy__sympy-13974` | `test_tensor_product_simp` 失败 | tensor product simplify 行为仍不正确 |
| `sympy__sympy-20916` | `test_super_sub` 失败 | 上下标渲染/解析行为仍不正确 |

其中 `sphinx-doc__sphinx-7985` 首次在 1800 秒处超时。使用相同模型补丁、不再调用模型，
把评测上限提高到 6000 秒后，测试在 1981.69 秒完成，最终确认是 `unresolved`，不是未知结果。

`pytest-dev__pytest-7205` 的汇总报告额外带有 `no_tests_collected` 模糊标记，但原始官方报告
明确记录了 9 个 FAIL_TO_PASS 失败，因此最终仍按真实测试失败处理。

### 2. 测试没有正常执行：2 题

| 实例 | 失败原因 |
|---|---|
| `pylint-dev__pylint-4551` | 测试收集阶段出现 ImportError：无法从 `pylint.pyreverse.utils` 导入 `get_annotation`，0 个测试实际执行 |
| `sphinx-doc__sphinx-8595` | 模型提交中创建/修改了测试文件，与官方 test patch 冲突；随后目标测试文件不存在，最终 0 tests collected |

这两题不是机器或 Docker 故障。它们都由模型补丁导致测试入口无法正常建立，所以仍计入失败。

### 3. diff 格式错误，补丁无法应用：6 题

| 实例 | 失败原因 |
|---|---|
| `astropy__astropy-13453` | diff 在第 37 行截断，`malformed patch` |
| `django__django-11163` | diff 在第 40 行格式错误，并在文件中途截断 |
| `django__django-14792` | SQLite hunk 头与内容不一致，第 121 行格式错误 |
| `django__django-15957` | `get_prefetcher` hunk 格式错误 |
| `django__django-16263` | diff 第 166 行格式错误 |
| `scikit-learn__scikit-learn-14894` | diff 在第 57 行截断，`malformed patch` |

这 6 题暴露的是提交协议可靠性问题，因此下一版 harness 应在正式评测前强制执行 diff
语法和可应用性检查，并让模型在原有预算内重新提交。当前实验不会事后修复这些 diff 或改分。

## 成本

| 项目 | 成本 | API 调用 |
|---|---:|---:|
| 当前 100 条最终轨迹 | $3.10541145 | 7,461 |
| 保留的 `django__django-15128` 首次超时尝试 | $0.15184442 | 209 |
| 实际总账单 | **$3.25725587** | **7,670** |

平均每题成本为 **$0.03257256**，平均每个成功题成本为 **$0.04343008**。官方 Docker 评测
不调用模型，因此延长 `sphinx-doc__sphinx-7985` 的评测没有增加模型费用。

## 最终产物

运行目录：`runs/verified-deepseek-v41-flash-sequential-20260912/`

- `instances/`：100 题的 trajectory 和完整 event log；
- `reports/`：官方评测报告；
- `final/summary.json`：最终数字；
- `final/failures.json`：25 个失败题；
- `final/manifest.json`：100 题逐题索引和全部评测尝试；
- `final/raw-evaluation-logs.tar.zst`：原始评测日志归档；
- `final/checksums.sha256`：全部产物校验和。

进入 `final/` 后运行以下命令，可以验证所有保留文件：

```bash
sha256sum -c checksums.sha256
```

本报告的最终数字是：**100 题，75 成功，25 失败；25 个失败由 17 个测试失败、2 个测试无法
正常执行、6 个非法 diff 构成。**
