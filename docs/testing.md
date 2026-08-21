# 测试策略

这篇记录本项目的测试是怎么从"跑一下 main.py 看看"演进到三层分层的。

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
env.execute.return_value = "fake output"     # 假装命令输出了这些

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

## 阶段 3：E2E 测试（全真）

```python
@pytest.mark.e2e
def test_simple_echo_task():
    agent = Agent(Model(), LocalEnvironment())  # 全都是真的
    result = agent.run("Run echo hello and submit")
    assert result["exit_status"] == "submitted"
```

**E2E 只回答一个问题**：模型真的理解我们 5 个工具（`bash`/`read`/`edit`/`write`/`submit`）的 JSON schema 吗？会正确地构造 `tool_calls` 吗？

剩下的（Agent 循环是否正确、execute 是否转发了 timeout、truncation 是否正确……）前三层已经全覆盖了。

**为什么只有 2 个 E2E**：每个 E2E 要调 API（花钱 + 等网络）。单元测试和集成测试已经把逻辑验证完了，E2E 不需要覆盖各种边界情况——那是前两层的职责。

---

## 汇总

```
       ╱‾‾‾‾‾╲         E2E:   2 个   真 API + 真 shell    30s    "模型理解工具吗?"
      ╱       ╲
     ╱ 集成    ╲       集成:  40 个   假 API + 真 shell     秒     "shell 输出正确解析吗?"
    ╱           ╲
   ╱  单元测试   ╲     单元:  179 个  假 API + 假 shell     ms     "每个函数行为对吗?"
  ‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾
```

三条原则：

1. **越底层越多**：从 179 → 40 → 2，不是反过来的
2. **每层测不同的事**：单元测逻辑、集成测编码、E2E 测 API schema——没有重叠
3. **每层的 mock 点不同**：单元全 mock、集成半 mock、E2E 不 mock

关于测试概念的详细解释（mock、ABC、工厂模式、happy path vs error path），见 [concepts.md](concepts.md)。
