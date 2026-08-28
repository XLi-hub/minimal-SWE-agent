# 学习文档

按主题组织，每个文件聚焦一个话题。和 README 区别：

- **README** — 项目文档：这是什么、怎么装、怎么用
- **docs/** — 学习笔记：为什么这样设计、用了什么知识、怎么学的

## 索引

| 文件 | 适合阅读时机 |
|---|---|
| [architecture.md](architecture.md) | 想理解项目为什么这样组织代码 |
| [config.md](config.md) | 想理解配置怎么从硬编码外置到 YAML / 环境变量 |
| [concepts.md](concepts.md) | 遇到不认识的术语时查阅 |
| [tool-calling.md](tool-calling.md) | 想理解从文本解析到 function calling 的演变 |
| [environment.md](environment.md) | 想理解 Docker 环境和注册表模式 |
| [swebench.md](swebench.md) | 想运行 SWE-bench 单例、批量任务和断点续跑 |
| [context-compression.md](context-compression.md) | 想理解上下文压缩和 LLM 摘要怎么触发 |
| [SWE-bench Verified 双实例复盘](experiments/swebench-verified-deepseek-v4-retrospective.md) | 想了解一次 2/2 结果背后的污染、轨迹审计和评测设计思考 |
| [SWE-bench 高难双实例 0/2 复盘](experiments/swebench-verified-hard-0-of-2-analysis.md) | 想理解自测全绿仍失败的原因、harness 边界和验证机制改进 |
| [SWE-bench 高难双实例审计复跑](experiments/swebench-verified-hard-audit-rerun.md) | 想比较 draft audit 前后轨迹，并理解为什么过程改善仍可能是 0/2 |
| [Clean-context review 续试](experiments/swebench-clean-review-followup.md) | 想理解去锚定仍失败的原因、按需完整轨迹和 harness 分层归因 |
| [testing.md](testing.md) | 想理解测试策略和分层的原因 |
| [faq.md](faq.md) | 有具体问题时查阅 |

## 建议阅读顺序

1. 先用 `minimal`（或 `python -m mini_agent`）跑一遍，感受 agent 是怎么工作的
2. 读 [architecture.md](architecture.md)，对照代码看每个模块的职责
3. 想改配置（步数、模型、prompt、价格）先读 [config.md](config.md)
4. 遇到不懂的术语就去 [concepts.md](concepts.md) 查
5. 想深入某个话题（如 function calling、Docker、测试策略）再点进去看
6. 有疑问去 [faq.md](faq.md) 找
