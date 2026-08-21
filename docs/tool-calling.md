# 工具调用演进

这篇记录本项目的 `bash` 工具调用从 v1（文本解析）到 v2（function calling）到 v3（显式 submit）的演变过程。

## v1 — 正则解析（已废弃）

最早的做法：模型输出自然语言混合代码块，用正则提取命令。

```
模型输出:
"好的，我来看看目录结构：

```bash-action
ls -la
```"

Agent 用正则提取:
>>> re.findall(r"```bash-action\s*\n(.*?)\n```", output)
["ls -la"]
```

问题：
- 模型有时不按格式写（忘了用 \`\`\`bash-action 或者用了别的标记）
- 格式错误时 agent 要提醒模型重试，浪费一轮对话
- 扩展难——加新工具需要定义新正则，越来越像手写编译器

## v2 — OpenAI Function Calling

DeepSeek 的 API 兼容 OpenAI 的 function calling 协议。模型不再输出自由文本，而是返回结构化的工具调用请求。

```json
// 模型返回:
{
  "choices": [{
    "message": {
      "content": "我来列出文件。",
      "tool_calls": [{
        "id": "call_1",
        "function": {
          "name": "bash",
          "arguments": "{\"command\": \"ls -la\"}"
        }
      }]
    }
  }]
}
```

Agent 不再需要 `parse_action()` 函数——`msg.tool_calls` 直接就是结构化的命令列表。

**新能力**：
- 模型可以一次请求调多个工具（如同时 `ls` 和 `cat`）
- `lines` 参数让模型自主控制返回行数
- 100% 准确——模型被训练来严格遵守 JSON schema

### 输出截断

长命令输出（如 `cat 大文件`）会撑爆上下文窗口。方案：

1. `BASH_TOOL` 新增可选参数 `lines`（默认 100）和 `timeout`（默认 30 秒）
2. Agent 执行后调用 `truncate_output()`——保留头尾各一半 + "[... X lines truncated ...]"
3. **v4 改进**：截断标记从被动提示变为主动引导——除了行数信息，还附带 `[WARNING]` 告诉模型可以调高 `lines` 或用 `head`/`tail`/`sed` 精确读
4. 模型可以为慢命令（`pip install` 等）指定更高的 `timeout` 值

### 超时处理：不杀进程

v2 版本用 `subprocess.run(timeout=...)`，超时直接 SIGKILL。问题：

- `pip install` 被杀 → 包安装了一半 → 系统状态被破坏
- 模型只看到 "timed out"，看不到已经跑出来的输出，无法判断是网慢还是卡死

**v4 改进**：改用 `Popen + communicate(timeout=...)`：

```
subprocess.run(timeout=30)  → 超时 → SIGKILL → 进程死，输出丢
Popen + communicate(30)     → 超时 → 进程继续 → 部分输出保留
```

超时后模型收到的消息包含三部分：
1. 已产生的部分输出（让模型判断进度）
2. `[STILL RUNNING]` 标识（明确告知进程还活着）
3. 操作指引：加 timeout 等完成 / 先 kill 再重来 / 不要直接重跑（会冲突）

```python
# 模型看到的消息示例
"""
Downloading torch-2.0.0... 45% 100MB/220MB
[STILL RUNNING: Command has been executing for 30s and is not finished yet.
The process is still alive. To wait for it, re-run with a higher 'timeout'
(e.g. timeout=60). To abort and restart, kill the old process first
(use 'ps aux | grep' to find its PID, then 'kill'). Do NOT re-run without
killing — two instances of the same command will conflict.]
"""
```

**为什么不杀是合理的**：bash 是万能工具——模型可以 `ps` 查 PID、`kill` 杀进程、加 timeout 等完成。agent 不需要替模型做这些决策。

### 超长时间任务与后台执行

`pip install` 最多几分钟，但训练模型、大规模构建可能需要几小时。目前有两种处理方式：

**方式一：用 `&` 后台执行 + `tail` 轮询**（bash 工具描述里已写入指引）

```bash
nohup python train.py &> /tmp/train.log & echo PID: $!
tail /tmp/train.log         # 看进度
grep "accuracy" /tmp/train.log  # 找关键指标
```

**方式二：agent 级后台任务**（讨论过，未实现）

理想方案：`bash(command="train.sh", background=True)` → agent 内部开线程 `proc.wait()` → 任务结束时自动往对话里注入消息。模型完全不用轮询。

**为什么没做**：
1. SWE-bench 风格的修 bug 任务不需要超长任务——读文件 → 改一行 → 跑测试，全是同步的，3 秒一个来回
2. 输出量不可控——训练日志动辄 10 万行，直接塞 messages 会炸上下文（Claude Code 用流式推送 + 前端截断来解决，那是整套基础设施）
3. 模型不适应异步——对话中间突然插一条系统通知，模型的训练数据里很少见到这种模式
4. 线程安全——主循环在读 messages，后台线程要写，需要加锁
5. `max_time=1800`（30 分钟）已经能覆盖绝大多数场景——`timeout=600` 同步等也比造一套后台系统简单

这不是能力问题，是取舍——作为 SWE agent 而非通用 agent，当前方案够用了。

## v3 — 显式 Submit 工具

v2 的退出机制：模型不调工具 = 退出。问题：
- 模型可能想继续但不敢调工具（怕循环停不下来）
- 无法区分"任务完成"和"模型卡住了"
- SWE-bench 需要精确提取 patch，不能从对话里猜

方案：给模型一个显式的 `submit(output=...)` 工具。

```python
SUBMIT_TOOL = {
    "type": "function",
    "function": {
        "name": "submit",
        "description": "Submit your final answer when the task is complete.",
        "parameters": {
            "type": "object",
            "properties": {
                "output": {"type": "string", "description": "Final answer or patch."}
            },
            "required": ["output"],
        },
    },
}
```

模型流程变成：

```
bash(command="cat buggy_file.py")    → 查代码
bash(command="sed -i ...")          → 修改
bash(command="git diff")            → 生成 patch
submit(output="diff --git ...")     → 显式提交
```

Agent.run() 返回结构化结果：

```python
{"exit_status": "submitted", "submission": "diff --git ...", "messages": [...]}
```

| exit_status | 含义 | 什么时候出现 |
|---|---|---|
| `submitted` | 正常完成 | 模型调了 submit |
| `no_tool_calls` | 意外退出 | 模型没调任何工具（fallback） |
| `max_steps` | 达到步数上限 | 循环到达 `max_steps` 限制（默认 250） |
| `max_time` | 达到时长上限 | 运行超过 `max_time`（默认 1800s） |
| `cost_limit` | 达到成本上限 | 累计成本超过 `cost_limit`（默认 3.0 USD） |
| `interrupted` | 用户打断 | Ctrl+C |
| `error` | 异常 | Agent 内部未处理的错误 |

**安全设计**：循环是 `while True`，但由**异常驱动**退出——每轮 `step()` 开头先查步数/时长/成本上限，超限抛 `MaxSteps`/`MaxTime`/`CostLimit` 异常，`run()` 捕获后退出循环，所以即使代码有 bug 也不会无限运行。模型可以通过 `timeout` 参数为慢命令（`pip install`, `git clone`）请求更长的超时时间。

## v4 — 专用文件工具 read/edit/write（参考 PI）

v1–v3 里模型一切文件操作都走 `bash`：`cat` 读、`sed -i` 改、heredoc 写。能用，但有隐患：

- `sed` 匹配不到时**静默成功**（exit 0、文件没变、模型还以为改好了）
- heredoc 里任意内容都要做 shell 转义，特殊字符易出错
- 轨迹里 `bash` 的命令是黑盒字符串，harness 无法追踪「改了哪个文件的哪一段」

参考 [PI](https://github.com/earendil-works/pi) 的做法，加了三个专用文件工具（现已**默认启用**，见 [config.md](config.md#内置两套工具配置)）：

| 工具 | 作用 | 失败语义 |
|---|---|---|
| `read(path)` | 读文件，带行号 | 文件不存在 → 明确报错 |
| `edit(path, old_string, new_string)` | 替换唯一一处 `old_string` | 缺失或出现多次 → **明确报错** |
| `write(path, content)` | 整文件创建/覆盖 | 内容走 stdin，不做 shell 转义 |

关键差异：`edit` 的 `apply_edit()` 对 `old_string` 做 `count==0` / `count>1` 校验——把原来 `sed` 的静默失败变成显式失败。想要回到纯 `bash` 路线，`python main.py --config default_bash` 即可（两套配置并存）。
