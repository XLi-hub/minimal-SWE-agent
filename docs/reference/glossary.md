# 术语表

本页解释项目中特别容易混淆的术语，并链接到实际边界。

## ABC

Abstract Base Class，Python 中由 `abc.ABC` 与 `@abstractmethod` 声明的接口约束。本项目的
Environment 是 ABC；缺少 `execute`、`read_file` 或 `write_file` 的子类不能实例化。

## Agent

控制循环。它维护 messages/events、检查预算、查询 Model、分发工具并决定退出，不等于
模型、shell 或 benchmark runner。[Agent 循环](../architecture/agent-loop.md)

## 鸭子类型

对象无需继承指定基类，只要提供调用方所需行为即可。Agent 对 Model 使用鸭子类型：测试
fake 只需提供兼容 `.query(messages, tools)` 和响应形状。

## Model adapter

把项目 messages/tools 转给 provider，并返回兼容响应的具体类。仓库 `Model` 使用 OpenAI
SDK 的 Chat Completions 接口；它不是抽象 Model 基类。

## Environment

命令与文件 I/O 的抽象执行边界。Local 直接操作宿主，Docker 操作长寿命容器。统一接口
包括 `execute`、`read_file`、`write_file`、`cleanup`。

## 依赖注入

在外部构造依赖并传给消费者，例如 `Agent(model, environment, config)`，而不是 Agent 内部
写死具体 Model 和 LocalEnvironment。它使替换和测试更直接。

## Factory

按名称或上下文选择具体实现的函数。`get_environment()` 按配置名称构造环境；benchmark
还允许注入 model/environment/agent factories。

## Tool schema

发给 provider 的 JSON function 描述，声明名称、说明和参数结构。schema 约束模型输出意图，
但运行时仍必须校验 provider 返回的 arguments。

## Handler

执行一个工具的 Python callable，接收已解析 args 与 ToolContext，返回 ToolResult。
schema/handler 通过 registry 成对注册。

## Observation

工具执行后追加的 `role=tool` 消息。成功输出、参数错误、policy 拒绝和 skipped 都是
observation；必须带回原 `tool_call_id`。

## Round-trip

一条 assistant(tool_calls) 及紧随其后的全部 tool observations。上下文压缩把它作为原子
单元，避免产生 provider 无法接受的孤儿调用。

## Submit

模型显式声明完成的工具。普通 profile 首次 submit 终止；review profile 首次只形成 draft，
再次 submit 才结束。

## Clean-context review

draft 后丢弃作者模型工作上下文，只保留原始 task、候选 patch、review prompt 和可选交接
材料，以降低锚定。它仍可能由同一模型执行，不等于独立 evaluator。

## Messages

下一次模型查询使用的 context view。它可被摘要替换，所以 `.traj.json.messages` 不代表完整
历史。[上下文与记录](../architecture/context-and-records.md)

## Events

一次运行内追加的原始消息与控制边界记录，最终保存为 `.events.jsonl`。压缩不会删除旧事件。

## Evidence

从 events 确定性抽取的机器可观察事实，如命令、return code、文件路径和 error。它有选择地
省略叙述，但不是模型 summary。

## Summary

模型生成的有损工作记忆，用来节省 context 或交接导航。即使结构化，也仍是不可信声明，
关键结论必须回到工具或 event journal 验证。

## Trajectory

广义上指运行记录；具体落盘时主 `.traj.json` 保存最终 context/metadata，sidecar
`.events.jsonl` 保存完整事件。[轨迹格式](trajectory-format.md)

## Context compression

当估算输入接近窗口时，把中间旧 round-trips 总结成 marker message，保留 system、原始
task 与最近单元。摘要调用计费，但不计主循环 step。

## ExecutionResult

Environment 命令返回的 mapping：`output`、`returncode`、`exception_info`。非零 return code
通常表示命令本身失败；`-1` 加 exception info 表示执行机械问题。

## `UNSET`

配置合并哨兵，表示高优先级层没有意见。它与 `None` 不同：`None` 是可真实覆盖下层的值，
例如关闭 max_time。

## Mock / Fake

替代真实依赖的测试对象。mock 常记录调用并由框架配置；fake 通常实现小型可运行行为。
两者都不能替代真实 shell quoting、Docker 生命周期或付费 provider E2E 的全部验证。

## Unit / Integration / E2E

unit 隔离模块，integration 组合真实 shell 与 fake model，Docker 层验证 daemon 资源，E2E
调用真实 provider。默认测试明确排除 E2E。[测试指南](../guides/testing.md)

## SWE-bench

以真实仓库 issue 和测试评估 patch 的 benchmark。generation 产出 prediction；官方 harness
另行评分。submitted、本地测试绿和 resolved 是不同状态。

## Harness

执行官方测试与汇总 resolved 的评测程序。它是 Agent 运行后的独立阶段，基础设施错误应与
patch 行为错误分开报告。

返回[文档首页](../index.md)。
