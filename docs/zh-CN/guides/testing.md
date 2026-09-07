# 测试指南

测试按风险边界分层：纯函数和 fake 优先，真实 shell 验证集成，Docker 验证资源层，真实模型
API 只在显式 E2E 中运行。数量会变化，因此文档不硬编码测试个数。

## 默认命令

仓库要求使用项目 conda 环境：

```bash
conda run -n minimal-SWE-agent env PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python -m pytest tests/ -q -p no:anyio -m "not e2e"
```

这条命令不会选择真实 provider E2E。若 CI 也没有 Docker daemon，可进一步排除：

```bash
conda run -n minimal-SWE-agent env PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python -m pytest tests/ -q -p no:anyio \
  -m "not e2e and not docker"
```

`pyproject.toml` 的 pytest 默认 addopts 也排除 `e2e`，但仓库约定仍使用上面的明确命令，
避免本机第三方 pytest plugins 改变行为。

## 纯单元层

这层不联网、不运行真实命令，覆盖：

- Agent 的预算、退出、批次确认和 review 状态机；
- context 分组、压缩与 token 估算；
- 配置 merge、环境变量、模板和 pydantic 校验；
- 工具 schema/handler、文件编辑和输出截断；
- 计费、证据抽取和 persistence；
- dataset、prediction storage 与 harness 命令构造。

Fake Model 应返回结构上兼容的 response，而不是绕过 Agent 的解析路径。Fake Environment
应返回 `ExecutionResult` 形态，必要时记录调用，用来断言预算命中后没有副作用。

## 集成层

`tests/test_integration.py` 等测试组合真 `LocalEnvironment` 与 fake Model，验证：

- shell quoting、stdout/stderr 和空输出；
- 多轮 assistant/tool 消息能被下一次 query 正确消费；
- 非零 return code 与 execution error 不混淆；
- 文件工具与 bash 观察同一宿主工作目录；
- timeout 后不会遗留子进程树。

真 shell 会暴露 mock 隐藏的问题，但仍不应访问网络或真实模型服务。

## Docker 层

带 `docker` marker 的测试需要可访问的 daemon，验证镜像启动、cwd、文件 I/O、环境转发、
timeout 和 cleanup。daemon 不可用时测试应跳过，而不是伪装成通过。

调试时先确认：

```bash
docker info
```

Docker 测试可能拉取镜像、消耗磁盘和较长时间。不要把普通单元测试的失败归因于 Docker；
先用 focused test 缩小范围。

## E2E 层

E2E 会调用真实 OpenAI-compatible API，可能产生费用，只有用户明确决定后才运行：

```bash
conda run -n minimal-SWE-agent env PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python -m pytest tests/test_e2e.py -v -p no:anyio -m e2e
```

运行前核对 provider、model、base URL、API key、token 单价、cost limit 和任务内容。发现
环境中恰好存在 key 不构成运行授权。

## 修改后的 focused tests

按改动选择最窄但足够的集合：

| 改动 | 首选测试 |
|---|---|
| Agent/submit/review | `tests/test_agent.py`、`tests/test_evidence.py` |
| tools/tooling | `tests/test_tools.py` |
| config/YAML | `tests/test_config*.py` |
| environments | `tests/test_environment.py`、`test_environments_init.py`、`test_docker.py` |
| context/trajectory | `test_context.py`、`test_persistence.py` |
| benchmark | `tests/benchmarks/` |
| CLI/resources | `tests/test_cli.py`、`tests/benchmarks/test_cli.py` |

focused tests 通过后，交付前再跑完整非 E2E suite。

## 应断言什么

高价值断言检查 observable contract，而不只检查“没有抛异常”：

- tool call id 和 tool response 一一对应；
- return code、异常信息与输出都准确；
- 命中时间/费用后 handler 未执行；
- 清理在成功与失败路径都发生；
- 保存的 event count 和 sidecar 内容一致；
- runner 的 prediction 和 status 可断点恢复；
- review 不把作者摘要误当机器证据。

## 失败解释

测试命令本身的 return code 才决定成功。`pytest | tail` 若没有 pipefail 可能显示尾部输出却
掩盖 pytest 失败；SWE-bench profile 因此启用 pipefail。缺依赖、找不到测试、timeout 和
skip 都需要准确报告，不能统称“测试通过”。

返回[文档首页](../index.md)，或继续读[设计取舍](../decisions/design-tradeoffs.md)。
