# 设计取舍

本页汇总项目明确选择和未实现范围。数字默认值可能变化，请查 YAML；这里记录为什么需要
这些机制，以及它们没有解决什么。

## 最小不等于单文件

Agent、Model、Environment、tooling、context/records 与 benchmark 分开，使每层可替换和
独立测试。代价是文件更多；收益是错误能定位到协议、provider、执行环境或 runner，而不是
只能跑昂贵 E2E 猜原因。

`tools.py` 与 `benchmarks/swebench.py` 保留 facade/orchestration，内部细节分别下沉到
`tooling/` 与 `_swebench/`。这比持续扩张单文件更符合当前复杂度。

## Model 鸭子类型，Environment ABC

Model provider 响应已经受 OpenAI-compatible 形态约束，测试 fake 常很小，因此 Agent 只按
`.query()` 鸭子类型调用。Environment 有多个实现且涉及命令、文件和清理，显式 ABC 能在
实例化时发现漏实现。

两者并不矛盾：接口形式取决于替换风险，而非追求对称。

## 独立 shell，而非持久会话

每次 bash 调用启动独立 shell。好处是 timeout、输出和 return code 边界清晰，Docker exec
与 Local 语义接近，没有隐藏的 shell cwd/export 状态。

代价是 `cd` 和 `export` 不跨调用保存；模型需要在命令中显式 `cd ... && ...`。容器文件系统
和后台进程可能持续，但 shell session 本身不会。

## Local 默认与 Docker 隔离

Local 启动快、适合教学和集成测试，却直接拥有宿主用户权限。Docker 隔离文件和进程，
但 daemon、mount、镜像和转发 secret 仍形成攻击面。

项目不把 Docker 称为绝对沙箱。普通 profile 不强制断网；SWE-bench 用 container network
none 做硬边界，再用命令识别给可理解的提前反馈。

## 同步命令，而非后台任务系统

同步调用让 event history 线性、消息写入不需要线程锁、timeout 和 cleanup 更容易推理。
长训练或大构建可由命令自行后台化并写日志，但 Agent 不提供任务句柄、通知和流式监控。

这限制了通用自动化能力，却符合以代码检查、编辑和测试为主的 SWE 任务范围。

## 三种预算，而非单一上限

- max steps 限制模型决策轮；
- max time 限制总墙钟；
- cost limit 按 usage 与配置单价限制估算美元。

它们覆盖不同失败模式。内置 Model 与支持 timeout 的 Environment 操作会接收剩余运行时间
作为请求 timeout，工具批次也会在调用之间重新检查 deadline。未声明 timeout 支持的第三方
同步 Model 或后端操作仍不能被外层墙钟检查抢占。默认价格为零时费用上限无效，因此时间和
步数仍是必要保护。

## Function calling，而非文本正则

Function calling 提供 call id、名称和参数边界，减少格式解析。代价是依赖兼容 API，而且
arguments 仍需校验。项目选择显式协议，详见[工具调用演进](tool-calling-evolution.md)。

## 专用文件工具，同时保留 bash

read/edit/write 避免 shell quoting 与模糊 `sed`，还能通过 Environment 对齐 local/Docker。
bash 仍用于搜索、测试和版本控制。两者共存增加工具选择，但更能表达操作意图和失败语义。

## 近似 token，而非 provider tokenizer

context 触发只需要保守近似，而真实计费使用 response usage。引入每个 provider 的 tokenizer
会增加依赖和模型映射复杂度，仍可能与服务端计算不同。

近似可能早压或晚压；reserve 和阈值就是为误差留空间，不应把估算值用于账单。

## messages 与 events 分离

工作 context 必须能压缩，审计记录必须保留原始事件。单列表无法同时满足。双视图增加
存储和理解成本，却能回答“模型最后看见什么”和“实际发生过什么”两个不同问题。

event journal 仍不是事务数据库：流式写 best-effort，最终文件各自原子但跨文件非事务。

## Review 降锚定，但不假装独立验证

clean-context review 丢弃作者工作上下文，携带候选 patch、机器 evidence 和可选不可信摘要。
这可促使重新检查，却可能由同一模型完成，也没有隐藏测试反馈。

因此它是提交门与实验变量，不是正确性证明。最终证据仍来自独立行为检查和官方 harness。

## 测试金字塔

unit/fake 快速覆盖分支，真 Local shell 验证 quoting 与进程行为，Docker 验证资源生命周期，
真实 provider E2E 验证最终兼容性。默认排除付费 E2E，避免“机器上恰有 key”触发费用。

Mock 不能证明真实集成，E2E 也不适合承担所有边界分支；分层是成本与置信度的折中。

## Prompt 规则

system 与 instance prompt 包含探索、最小修改、错误恢复、验证和 submit 约定。这些规则能
减少常见失败，但可能让模型行为偏向特定工作流。

Prompt 放在 YAML，允许普通使用和 benchmark 分别演进；测试应验证协议边界，不把 prompt
文字本身当作可靠安全机制。

## 非目标

- 多 provider 原生 SDK 抽象；
- production TUI、远程队列或后台任务管理；
- 完整操作系统级沙箱；
- 自动证明 patch 正确；
- 用少量 SWE-bench 样本宣称通用能力；
- 将历史实验结论当作当前默认配置。

系统全貌见[架构总览](../architecture/overview.md)，实验性结论见
[实验索引](../experiments/index.md)。
