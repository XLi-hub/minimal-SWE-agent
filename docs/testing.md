# 测试策略

这篇记录本项目的测试是怎么从"跑一下 main.py 看看"演进到三层分层的。

## 日常命令

本地项目环境使用仓库约定的 conda 环境运行非 E2E 测试：

```bash
conda run -n minimal-SWE-agent env PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python -m pytest tests/ -q -p no:anyio -m "not e2e"
```

仓库的 pytest 默认参数已经包含 `-m 'not e2e'`，因此裸运行 `pytest` 也
不会因为本机存在 API key 而意外产生费用。Docker daemon 测试另外标记为
`docker`，没有 daemon 时会自动跳过；只跑普通 CI 测试可以使用：

```bash
python -m pytest tests/ -q -m "not e2e and not docker"
```

CLI 进程退出码也反映 Agent 的结果：`submitted` 为 `0`；`error`、
`no_tool_calls`、`max_steps`、`max_time`、`cost_limit` 和 `interrupted`
均为非零（其中中断使用惯例值 `130`）。这样脚本或 CI 可以可靠地区分
真正提交和提前停止。

查看当前安装版本：

```bash
minimal --version
```

E2E 只能通过显式 marker 选择，且仍需要配置 provider key：

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/ -v \
  -m e2e -p no:anyio
```

---

## 阶段 0：手动测试（没写任何测试）

项目最初只有 `main.py`，验证方式是：

```bash
python main.py
# Task: list the files in src/
# 看输出有没有 ls 的结果
```

问题跟所有手动测试一样——改了一行代码，懒得再跑一遍 `main.py`（要联网、等 10 秒、花钱）。很快就不测了。

---

## 阶段 1：单元测试（mock 一切）

第一个突破：**Agent 循环逻辑可以完全脱离 API 和 shell 测试**。

```python
# 不需要联网
# 不需要执行命令
# 只需要 MagicMock
model = MagicMock()
model.query.return_value = fake_response    # 假装模型说了这些

env = MagicMock()
env.execute.return_value = {                 # 假装执行器返回结构化结果
    "output": "fake output",
    "returncode": 0,
    "exception_info": "",
}

agent = Agent(model, env)
result = agent.run("test")
assert result["exit_status"] == "no_tool_calls"
```

这一层能测什么：

| 测试 | 测的是什么 |
|---|---|
| `test_exits_when_no_tool_calls` | 模型不说人话就直接退出 |
| `test_submit_exits_with_submission` | submit 工具调用后返回正确结果 |
| `test_recovers_from_execution_error` | 命令执行报错不崩溃 |
| `test_keyboard_interrupt` | Ctrl+C 能优雅退出 |
| `test_exits_with_max_steps` | 循环不会无限跑 |
| `test_custom_timeout_from_tool_call` | timeout 参数被正确传递 |
| `test_truncation` | 长输出被截断 |
| `test_run_accumulates_cost` | 每次模型调用后累加成本 |
| `test_cost_limit_stops_agent` | 成本超限时停止 |

`compute_cost`（token → USD 换算）甚至不需要 mock 任何外部对象——构造一个带 `usage` 的
假 response 就够了，见 `tests/test_cost.py`。

**好处**：毫秒级，0 元，可以每改一行就跑一次。

**缺陷**：mock 的 `fake output` 太理想化——真实 shell 的输出可能为空、可能有反斜杠、可能 stderr 和 stdout 混在一起。

---

## 阶段 2：集成测试（一半真，一半假）

方案：**用真 `LocalEnvironment`，只 mock Model**。Agent 真的执行 `subprocess.run`。

```python
# 真 shell + 假模型
model = MagicMock()
model.query.side_effect = [
    bash_response,   # Round 1: 执行 ls
    submit_response  # Round 2: 提交
]
agent = Agent(model, LocalEnvironment())   # ← env 是真的
result = agent.run("list files")
```

这层抓住了 mock 永远抓不到的问题：

**1. 编码问题**

```python
# 测试：输出里有反斜杠和引号
bash("printf '%s' 'path\\to\\file \"quoted\" ünicode'")
# mock 测试永远不会有 backslash 问题——因为 fake output 是 Python 字符串，
# 不存在"shell 解释"这回事
```

**2. 空输出**

```python
# 测试：true 命令无输出
bash("true")
# mock 测试通常 return_value="some text"，没测过空字符串路径
```

**3. stderr 混入 stdout**

```python
bash("python -c 'import sys; sys.stdout.write(\"out\"); sys.stderr.write(\"err\")'")
# mock 不可能同时模拟两个流的交互
```

**4. 长输出截断**

```python
bash("seq 1 300")   # 300 行 → 触发截断
# mock 的 return_value 是构造好的，truncation 的 "first line ... last line" 
# 逻辑是否正确——只有真输出能验证
```

**5. 多步交互**

```python
# Round 1: pwd
# Round 2: echo step2 done
# Round 3: submit
# 三个真 shell 输出连续进入 messages——验证消息历史格式没有累积错误
```

**好处**：秒级，不花钱，但能抓到 mock 抓不到的 bug。

**缺陷**：不能验证模型真的理解工具 schema——因为模型本身被 mock 了。

---

## 阶段 3：E2E 测试（全真，显式 opt-in）

E2E 默认使用项目的模型配置。若要在 VS Code Test Explorer 或命令行中
测试其他 OpenAI-compatible 供应商，可在项目根目录的 `.env` 中设置：

```dotenv
E2E_MODEL_NAME=provider-model
E2E_BASE_URL=https://provider.example/v1
E2E_API_KEY_ENV=PROVIDER_API_KEY
PROVIDER_API_KEY=your-key
```

`.env` 会被 VS Code Python 扩展和 E2E 测试加载；`E2E_API_KEY_ENV` 只保存
密钥变量名，真实密钥仍放在它指向的环境变量中。这些 `E2E_*` 变量只影响
E2E，不会覆盖日常 CLI 或普通测试的配置。

```python
@pytest.mark.e2e
def test_simple_echo_task():
    agent = Agent(Model(), LocalEnvironment())  # 全都是真的
    result = agent.run("Run echo hello and submit")
    assert result["exit_status"] == "submitted"
```

**E2E 只回答一个受限的问题**：在真实 API 和本地 shell 组成的链路中，模型能否正确理解
并调用当前 E2E 任务实际使用的 `bash` 与 `submit` schema。E2E 不覆盖
`read`/`edit`/`write` 的真实模型调用；这些工具的参数校验、handler 和环境交互由单元及
集成测试覆盖。

剩下的（Agent 循环是否正确、execute 是否转发了 timeout、truncation 是否正确……）前两层已经全覆盖了。

**为什么只有 2 个 E2E**：每个 E2E 要调 API（花钱 + 等网络）。单元测试和集成测试已经把逻辑验证完了，E2E 不需要覆盖各种边界情况——那是前两层的职责。pytest 默认使用 `-m 'not e2e'`，即使环境中有 API key 也不会执行；只有明确传入 `-m e2e` 才会 opt in。

---

## 汇总

```
       ╱‾‾‾‾‾╲         E2E:   最少   真 API + 真 shell    慢/付费  "模型会用 bash/submit 吗?"
      ╱       ╲
     ╱ 集成    ╲       集成:  适量   假 API + 真 shell     秒级    "shell 输出正确解析吗?"
    ╱           ╲
   ╱  单元测试   ╲     单元:  最多   假 API + 假 shell     毫秒级  "每个函数行为对吗?"
  ‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾
```

三条原则：

1. **越底层越多**：大量单元、适量集成、极少 E2E，不是反过来的
2. **每层测不同的事**：单元测逻辑、集成测编码、E2E 测 `bash`/`submit` 的真实 API schema——没有重叠
3. **每层的 mock 点不同**：单元全 mock、集成半 mock、E2E 不 mock

关于测试概念的详细解释（mock、ABC、工厂模式、happy path vs error path），见 [concepts.md](concepts.md)。
