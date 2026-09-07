# 工具调用演进

本页记录工具协议为何从文本解析演进到 function calling、显式 submit、专用文件工具、
registry 与 review 回查。它解释历史，不替代当前[工具参考](../reference/tools.md)。

## v1：正则解析文本块（已删除）

早期模型输出自然语言和特定 fenced code block，Agent 用正则提取 shell 命令。优点是兼容
只会输出文本的模型；缺点是标签拼写、围栏、多个命令和解释文本都会让 parser 变脆弱。

解析失败还要再注入纠正消息，既浪费步骤，也把“模型没按模板写”与“命令执行失败”混成
一种错误。新增工具意味着新增文本语法，最终会形成一个不完整的自制协议。

## v2：OpenAI function calling

模型改为返回 `tool_calls`：每项含 id、function name 和序列化 arguments。Agent 不再从
自然语言猜命令，schema 还能向模型描述参数。

结构化不等于可信。`arguments` 仍可能是畸形 JSON、数组或标量；名称可能未知，字段类型也
可能错误。因此 dispatcher 必须运行时校验，并为原 call id 追加 error observation。

这一步建立了首个核心不变量：每个 assistant tool call 都要有对应 tool response，即使调用
无效也不能让轨迹断裂。

## v2.1：有界输出与 timeout

bash 加入 `lines` 和 `timeout`，随后又补字符预算，避免 minified JSON、base64 等超长单行
绕过行限制。截断保留头尾并给继续读取建议。

Environment 的命令结果从裸字符串升级为 `ExecutionResult`，区分 output、return code 与
执行器异常。Local timeout 终止整个进程组；SWE-bench shell 加 `pipefail`，防止 pipeline
末项的零状态掩盖前序测试失败。

项目没有建立异步后台任务系统。需要长命令时显式提高单次 timeout；这是以较小控制面换取
可预测线性事件历史的取舍。

## v3：显式 submit

“模型没有调工具”无法区分完成、卡住或截断。`submit(output)` 把完成变成显式协议事件，
`Agent.run()` 因而可以返回结构化 `exit_status` 与 submission。

循环同时引入步数、墙钟和费用退出。查询后必须再检查时间/成本，避免 provider 请求本身已
越界后仍执行写文件或 shell。批次中 submit 后的调用被确认但跳过，维护协议又避免副作用。

## v4：read/edit/write

一切走 bash 虽小，却有两个问题：shell quoting 使写文件脆弱，`sed` 等工具可能匹配失败却
给出误导状态。专用文件工具把操作放到 Environment 接口：

- read 提供行号和分页；
- edit 要求 old string 唯一匹配；
- write 明确整文件覆盖。

纯 bash profile 仍可用作教学对照，但默认配置启用专用文件工具。它们让 local/Docker 文件
语义一致，也让事件证据能直接识别文件路径。

## v4.1：schema/handler registry 与 tooling 拆分

随着工具增加，分散的 schema list 和名称 if/elif 会漂移。当前 `TOOL_REGISTRY` 将 name、
schema、handler 组成 `ToolDefinition`，配置名单同时控制模型可见性和执行授权。

随后可复用细节移到 `tooling/`：schemas、types、files、output、network。`tools.py` 保留
handlers 与 runtime dispatch，从“所有逻辑单文件”变为稳定 facade + 小模块。

## v5：draft review 与 trajectory

首次 submit 可被捕获为 draft，再要求模型审查后提交。为了降低作者推理锚定，SWE-bench
profile 可以重置 messages；但完全清空会浪费此前探索。

当前折中由三层组成：

1. append-only events 保留原始记录；
2. evidence checkpoint 只抽机器事实；
3. `trajectory` 允许 reviewer 按需分页/筛选回查，而非自动塞回全部历史。

可选模型 summary 只作为明确不可信的 navigation aid。reviewer 没有隐藏 evaluator 反馈，
因此 review 改善的是验证过程，不保证 patch 正确。

## 当前不变量

- registry 名称与 schema function name 一致；
- `tools.enabled` 同时控制 advertise 和 authorize；
- arguments 必须解析为 JSON object；
- 每个 call id 都有 observation；
- submit/limit 后不执行剩余副作用；
- 文件 I/O 走 Environment；
- observation 有字符预算；
- trajectory 查询只读且有界；
- assistant summary 不被当作 machine evidence。

## 明确代价

Function calling 绑定兼容 provider 格式；专用工具增加 schema 面积；review 多一次或多次模型
调用；完整事件增加磁盘和隐私负担。项目接受这些成本，因为协议可审计性和失败诊断是教学
目标的一部分。

当前实现见[工具系统](../architecture/tool-system.md)，参数见[工具参考](../reference/tools.md)。
