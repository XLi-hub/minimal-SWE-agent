# 学习文档

按主题组织，每个文件聚焦一个话题。和 README 区别：

- **README** — 项目文档：这是什么、怎么装、怎么用
- **docs/** — 学习笔记：为什么这样设计、用了什么知识、怎么学的

## 索引

| 文件 | 适合阅读时机 |
|---|---|
| [architecture.md](architecture.md) | 想理解项目为什么这样组织代码 |
| [concepts.md](concepts.md) | 遇到不认识的术语时查阅 |
| [tool-calling.md](tool-calling.md) | 想理解从文本解析到 function calling 的演变 |
| [environment.md](environment.md) | 想理解 Docker 环境和注册表模式 |

## 建议阅读顺序

1. 先用 `python main.py` 跑一遍，感受 agent 是怎么工作的
2. 读 [architecture.md](architecture.md)，对照代码看每个模块的职责
3. 遇到不懂的术语就去 [concepts.md](concepts.md) 查
4. 想深入某个话题（如 function calling、Docker）再点进去看
