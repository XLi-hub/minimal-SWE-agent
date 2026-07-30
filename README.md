# minimal-SWE-agent

> 跟着 [minimal-agent.com](https://minimal-agent.com) 教程，从零构建一个终端 AI agent。

## 项目结构

```
minimal-SWE-agent/
├── src/mini_agent/          # 源代码
│   ├── model.py             #   query_lm    — 调用 DeepSeek API
│   ├── parser.py            #   parse_action — 正则提取 bash 命令
│   ├── environment.py       #   execute_action — subprocess 执行命令
│   └── agent.py             #   主循环 while True: 查 → 解 → 执行 → 反馈
├── tests/                   # 测试（与 src 一一对应）
│   ├── test_model.py        #   3 个: 非空返回 / 简单问答 / 多轮对话
│   ├── test_parser.py       #   5 个: 正常提取 / 无命令 / 多行 / 多块 / 边界
│   ├── test_environment.py  #   5 个: echo / pwd / ls / stderr / 错误命令
│   └── test_agent.py        #   2 个: exit退出 / 执行命令（集成测试，mock LM）
├── main.py                  # 入口：输入 task，启动 agent
└── run_tests.py             # 一键运行全部测试
```

## 环境准备

```bash
# Python 3.10+，创建独立环境
conda create -n minimal-SWE-agent python=3.10
conda activate minimal-SWE-agent

# 依赖
pip install openai pytest httpx
```

## 运行测试

```bash
python run_tests.py
```

### 已知问题

| 问题 | 原因 | 状态 |
|---|---|---|
| `httpx` 报 `Unknown scheme: socks://` | Clash 设了 `ALL_PROXY=socks://...`，httpx 只认 `socks5://` | `model.py` 已显式传 http_client 绕过 |
| `PYTHONPATH` 混入 ROS 的 pytest 插件 | ROS 的 launch_testing 与项目无关 | `run_tests.py` 已自动清理 |

## 启动 Agent

```bash
python main.py
```

输入任务，例如 `List the files in the current directory`，agent 会循环执行命令直到 LM 返回 `exit`。

## Agent 工作流程

```
用户任务
  ↓
system prompt + user message
  ↓
┌──────────────────────────┐
│  query_lm(messages)      │  ← 调用 DeepSeek API
│         ↓                │
│  parse_action(lm_output) │  ← 正则提取 ```bash-action ... ```
│         ↓                │
│  action == "exit"? ──→ break
│         ↓ 否             │
│  execute_action(action)  │  ← subprocess.run 执行
│         ↓                │
│  messages.append(output) │  ← 把结果喂回 LM
└──────────────────────────┘
```

## 参考

- [minimal-agent.com](https://minimal-agent.com) — 原教程
- [mini-swe-agent](https://github.com/swe-agent/mini-swe-agent) — 生产级实现
