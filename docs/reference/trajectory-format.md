# 轨迹格式参考

指定 `Agent.run(..., output="run.traj.json")` 或 CLI `-o` 后，会生成主 trajectory 与相邻
event journal。主文件当前格式标识为 `mini-agent-0.2`。

## 文件命名

| trajectory path | event sidecar |
|---|---|
| `run.traj.json` | `run.events.jsonl` |
| `result.json` | `result.events.jsonl` |

sidecar 路径写成相对主文件的 basename，移动产物时应成对移动。

## 主文件顶层

```json
{
  "trajectory_format": "mini-agent-0.2",
  "messages": [],
  "event_log": {
    "path": "run.events.jsonl",
    "format": "mini-agent-events-0.1",
    "event_count": 42
  },
  "info": {}
}
```

`messages` 是运行结束时的模型 context view，可能已压缩；它不是完整历史。内存中的
`serialize()` 可暂含 `events`，但 `save_trajectory_data()` 落盘时会移到 sidecar。

## info

`info` 包含：

- `exit_status`：submitted/no_tool_calls/max_steps/max_time/cost_limit/interrupted/error；
- `submission`：最终接受的输出，未提交时为空；
- `error`：非预期异常的 type、message、traceback，其他终局通常为 null；
- `mini_version`：生成轨迹的包版本；
- `event_log_stream_error`：运行中追加 sidecar 的 I/O 错误；
- `model_stats.api_calls`：主查询加摘要查询；
- `model_stats.instance_cost`：按配置价格估算的累计 USD；
- `config`：agent/model/environment 的 resolved 配置和具体类路径。

配置快照可能包含 prompt 和 provider endpoint，但设计上 `api_key_env` 只有密钥变量名。仍应
在分享前审查自定义 prompt、task、commands、输出与环境变量相关内容。

## messages

使用 OpenAI-compatible role 结构：

```json
{"role": "system", "content": "..."}
{"role": "user", "content": "..."}
{"role": "assistant", "content": null, "tool_calls": [...]}
{"role": "tool", "tool_call_id": "call_1", "content": "..."}
```

压缩摘要是带配置 marker 的 user message。每个 assistant tool call 应有对应 tool message；
被 submit 或运行限制跳过的调用也会得到明确 skipped observation。

## event journal

sidecar 每行一个 JSON object，至少含：

```json
{"sequence": 0, "type": "message", "message": {"role": "system", "content": "..."}}
```

sequence 从零递增。常见 type：

| type | payload 重点 |
|---|---|
| `message` | 未被压缩覆盖的原始消息 |
| `context_compression` | 压缩前后数量、summary、下一次查询的 context snapshot |
| `submission_review_checkpoint` | handoff summary 状态 |
| `submission_review_context_reset` | checkpoint 开关、summary 状态、review context snapshot |

事件格式允许增加字段和新 type。消费端应忽略未知字段，并对不认识的事件保持可见，而不是
丢弃整个 journal。

## 写入语义

运行开始时 sidecar 被初始化，事件随后 best-effort 追加。终局保存时：

1. 内存 events 序列化为完整 JSONL；
2. 临时文件 flush + fsync；
3. `os.replace` 原子替换 sidecar；
4. 主 trajectory 以相同方式单独原子替换。

单文件替换是原子的，但两个文件不是同一个事务。崩溃时可能只有其中一个是最新版本。

## 完整性检查

消费轨迹时：

1. 校验 `trajectory_format`；
2. 解析 `event_log.path`，拒绝意外越出产物目录的路径；
3. 校验每行 JSON 与 sequence 单调；
4. 对比实际行数和 `event_count`；
5. 验证 assistant call ids 与 tool results；
6. 若 `event_log_stream_error` 非空，注明运行时观测降级；
7. 不把 `messages` 数量当作原始事件数量。

## SWE-bench 附加 metadata

runner 在顶层 `instance` metadata 中只保留公开任务与 setup 字段，例如 instance id、
repo、base commit、problem statement、版本和 image。gold patch、隐藏测试、eval script 不应
进入 trajectory。

## 隐私与信任

events 可能包含源代码、shell 输出、文件路径、错误、模型推理和候选 patch。分享前按敏感
数据处理。assistant content 是声明；tool output 也可能来自不可信仓库，不能当作指令执行。

概念区别与 review 用法见[上下文与记录](../architecture/context-and-records.md)。
