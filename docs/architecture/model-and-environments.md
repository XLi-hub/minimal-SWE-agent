# 模型与执行环境

Model 和 Environment 都能替换，但它们的契约强度不同：仓库的 `Model` 是一个具体
OpenAI-compatible adapter，Agent 通过鸭子类型使用它；Environment 则是显式 ABC。

## Model：具体 adapter，鸭子类型边界

[`model.py`](../../src/mini_agent/model.py) 中的 `Model`：

1. 从 `ModelConfig` 读取 model name、base URL、密钥环境变量名和额外参数；
2. 构造 OpenAI client；
3. 通过 Chat Completions `create(...)` 发送 messages 和可选 tools；
4. 原样返回 provider response，由 Agent 解析 choice 与 usage；
5. 通过 `close()` 释放 HTTP 连接。

它不是抽象基类，也没有项目内的 `ModelProtocol`。`Agent` 只假设对象提供：

```python
model.query(messages, tools=None) -> OpenAI-compatible response
```

因此单元测试可注入 fake 或 mock，只要响应包含兼容的 `choices[0].message` 与必要 usage。
“可替换模型”在这里指鸭子类型与 OpenAI-compatible 响应，不代表所有 provider 都无需适配。

Model secret 不进入配置值；`api_key_env` 只保存环境变量名。provider 的真实模型参数、
base URL 和单价由使用者配置，权威字段见[配置参考](../reference/configuration.md)。

## 成本记账

[`cost.py`](../../src/mini_agent/cost.py) 从 response usage 读取 input、cached input 和 output
token，按每百万 token 单价计算。主查询与压缩摘要使用同一记账函数。

默认 YAML 的价格为零，因为兼容 provider 的计费不同。这意味着默认 `cost_limit` 数值本身
不能阻止真实费用；只有配置非零且正确的价格后，累计美元上限才有意义。

## Environment：显式 ABC

[`environments/base.py`](../../src/mini_agent/environments/base.py) 定义：

```python
execute(command, timeout) -> ExecutionResult
read_file(path) -> str
write_file(path, content) -> None
cleanup() -> None
```

前三个方法为抽象方法；`cleanup()` 有 no-op 默认实现，持有外部资源的子类应覆盖。
`ExecutionResult` 是 mapping，规范键为 `output`、`returncode`、`exception_info`。

命令非零退出是正常观察，写入 `returncode`；启动失败、timeout 或运行器错误使用
`returncode=-1` 和 `exception_info`。Environment 负责捕获 stdout/stderr 合流，工具层负责
格式化和截断。

## LocalEnvironment

Local 通过宿主 shell 启动子进程，并继承当前 Python 进程的工作目录。文件工具也相对同一
工作目录解析，因此 bash 与 read/edit/write 看到同一棵树。

POSIX 下命令在独立 session/process group 中运行。timeout 时实现会终止整个进程组并回收
输出，避免只杀 shell 却遗留子进程。Windows 走相应的进程树终止回退。

安全含义很直接：模型可以运行当前用户有权运行的命令、读取数据并写文件。本地模式不是
沙箱，输出截断也不限制命令本身的权限。

## DockerEnvironment

Docker 实现在初始化时确保镜像可用，启动一个长寿命容器，再通过 `docker exec` 执行多次
命令。这样保留容器内文件修改，同时避免每个工具调用重新创建容器。

文件 I/O 也通过容器内命令完成，路径相对配置的 `cwd`。解释器、容器存活时长、镜像拉取
超时、`run_args` 和有限的环境变量转发都由 EnvironmentConfig 控制。

`cleanup()` 停止并移除容器；调用应当放在 `finally`。Docker daemon 权限通常很高，错误的
mount、privileged 参数或 secret 转发仍可能破坏隔离假设。

## Factory 与依赖注入

`get_environment(name, **kwargs)` 用注册映射选择 `local` 或 `docker`。CLI 先构造 typed
config，再把 `config.environment.type` 交给 factory。Agent 不包含环境名称分支。

新增环境时：

1. 继承 `Environment` 并实现三个抽象操作；
2. 对任何获取的外部资源实现幂等或至少安全的 `cleanup()`；
3. 在 factory mapping 注册名称；
4. 增加构造、命令结果、文件一致性、timeout 与清理测试；
5. 如需新配置字段，更新 pydantic model 和 YAML。

## 资源所有权

普通 CLI 创建 Model 与 Environment，因此在 `finally` 中先 `model.close()`、后
`environment.cleanup()`，清理异常只警告，不覆盖真正的运行异常。

SWE-bench 每个实例各自拥有模型与容器；构造中途失败、Agent 失败或成功结束都要释放。
若用户直接构造对象，库不会替用户推断生命周期，调用者应显式清理。

## 安全检查表

- Local：只用于可信任务和可丢弃工作树；
- Docker：检查镜像来源、mount、run args、cwd 与转发环境变量；
- 网络：命令级 blocklist 不是边界，容器网络策略才是；
- secrets：YAML 只放环境变量名，不放值；
- timeout：终止子进程树，但不能回滚已发生的副作用；
- cleanup：释放资源，不等于撤销文件修改。

相关页面：[架构总览](overview.md)、[工具系统](tool-system.md)、
[设计取舍](../decisions/design-tradeoffs.md)。
