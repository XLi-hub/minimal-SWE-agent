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

1. `BASH_TOOL` 新增可选参数 `lines`（默认 100）
2. Agent 执行后调用 `_truncate_output()`——保留头尾各一半 + "[... X lines truncated ...]"
3. 模型看到截断标记后，可以加大 `lines` 值或用 `head`/`tail`/`sed` 精确读

这比固定截断好——模型知道自己想要多少上下文。

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
| `interrupted` | 用户打断 | Ctrl+C |
