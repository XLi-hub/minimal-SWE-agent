# 文档首页

文档按“架构、指南、参考、决策、实验”组织。README 只负责定位和启动；这里提供三条
阅读路径。目录最多两级，页面之间优先链接而不是复制同一段说明。

## 路径一：理解系统

适合第一次读代码，目标是从全貌走到关键机制。

1. [架构总览](architecture/overview.md)：七个部分、依赖方向和两条执行流程；
2. [Agent 循环](architecture/agent-loop.md)：预算、查询、工具批次与 submit review；
3. [工具系统](architecture/tool-system.md)：registry、schema/handler 与协议不变量；
4. [模型与环境](architecture/model-and-environments.md)：鸭子类型、ABC、local/Docker；
5. [上下文与记录](architecture/context-and-records.md)：messages、events、evidence、持久化；
6. [Benchmark 层](architecture/benchmark-layer.md)：runner 与 `_swebench/` 的边界。

读完后，可用[术语表](reference/glossary.md)补齐 ABC、依赖注入、mock、round-trip 等概念。

## 路径二：运行与修改

适合准备在本地使用、改配置、加工具或跑测试。

1. [配置指南](guides/configuration.md)：覆盖配置和保护 secrets；
2. [配置参考](reference/configuration.md)：六个配置 section 的字段职责；
3. [工具参考](reference/tools.md)：内置工具参数、返回与扩展步骤；
4. [测试指南](guides/testing.md)：unit、integration、Docker、E2E 的选择；
5. [SWE-bench 指南](guides/swebench.md)：单实例、批量和官方评分。

默认值以代码库中的 YAML 为准，不在页面中维护第二份完整副本：

- [普通 profile](../src/mini_agent/config/default.yaml)
- [SWE-bench profile](../src/mini_agent/config/benchmarks/swebench.yaml)

## 路径三：审计与设计复盘

适合分析一次运行为什么成功或失败，以及理解当前取舍的来由。

1. [轨迹格式](reference/trajectory-format.md)：context view、event journal 和 metadata；
2. [上下文与记录](architecture/context-and-records.md)：压缩、证据 checkpoint 与回查；
3. [工具调用演进](decisions/tool-calling-evolution.md)：从文本解析到 registry 与 review；
4. [设计取舍](decisions/design-tradeoffs.md)：安全、预算、shell 会话与测试边界；
5. [实验索引](experiments/index.md)：SWE-bench 实验与失败复盘。

审计时先区分“模型说了什么”和“机器观察到了什么”。assistant 的文字是声明；工具调用、
return code、文件操作和 harness 报告才是可独立核对的证据。

## 页面地图

```text
architecture/  系统为何这样拆、运行时如何流动
guides/        怎样配置、测试和运行 benchmark
reference/     字段、工具、文件格式和术语的查表页
decisions/     历史演进与明确接受的取舍
experiments/   有时间背景的实验记录，不充当当前行为规范
```

若文档与实现冲突，以源码、配置模型和 YAML 为准，并把差异视为需要修复的文档 bug。

## 快速查找

| 我想知道 | 去哪里 |
|---|---|
| 一轮查询为什么在工具前后都检查预算 | [Agent 循环](architecture/agent-loop.md) |
| schema、handler 和启用名单怎么对应 | [工具系统](architecture/tool-system.md) |
| Local 与 Docker 的文件路径语义 | [模型与环境](architecture/model-and-environments.md) |
| 压缩后怎样找回原始命令 | [上下文与记录](architecture/context-and-records.md) |
| prediction、status 和轨迹由谁保存 | [Benchmark 层](architecture/benchmark-layer.md) |
| 某个 YAML 字段允许什么值 | [配置参考](reference/configuration.md) |
| 某个工具接受哪些参数 | [工具参考](reference/tools.md) |
| `.traj.json` 与 `.events.jsonl` 的区别 | [轨迹格式](reference/trajectory-format.md) |
| 为什么没有持久 shell 或后台任务系统 | [设计取舍](decisions/design-tradeoffs.md) |

## 文档维护约定

- 行为说明优先链接源码或权威 YAML，不复制完整默认配置；
- experiments 记录当时观察，不反向定义当前架构；
- 新模块需要更新架构地图，新字段/工具/格式需要更新对应 reference；
- README 和所有 docs 的本地 Markdown 链接由 `tests/test_docs.py` 检查；
- 不校验标题 slug anchor，重命名标题时仍需人工检查跨页 anchor。

返回[项目 README](../README.md)。
