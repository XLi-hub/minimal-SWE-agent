# 架构图维护

本目录只放跨模块关系图。单页内部的短流程优先使用 Mermaid，避免每次改一个分支都要同步
二进制或大段 XML。

## 文件约定

| 文件 | 用途 |
|---|---|
| `system-overview.drawio` / `.svg` | 七个部分、核心依赖与 Benchmark 边界 |
| `context-records.drawio` / `.svg` | messages、events、evidence 与落盘产物的关系 |

`.drawio` 是可编辑源文件，`.svg` 是文档引用的发布版本。SVG 导出时也嵌入 diagram 数据，
因此可以直接在 draw.io 中打开；发生差异时仍以 `.drawio` 为准。

## 编辑与导出

1. 用 draw.io Desktop 或 diagrams.net 打开 `.drawio`；
2. 保持节点短句化，把解释留在相邻架构页面；
3. 导出时启用 **Include a copy of my diagram**，使用白色背景并裁剪；
4. 覆盖同名 `.svg`，检查文字没有截断、连线没有穿过节点；
5. 同时提交源图、SVG 和受影响的架构文档。

桌面版也可命令行导出：

```bash
drawio --export --format svg --embed-diagram --theme light \
  --embed-svg-fonts false --border 12 \
  --output docs/diagrams/system-overview.svg \
  docs/diagrams/system-overview.drawio
```

配色表达边界而非装饰：蓝色是编排，紫色是模型适配，绿色是外部执行或可核查事实，黄色是
上下文/记录，红色是 Benchmark 或 review 边界。新增颜色前先判断现有语义是否足够。

返回[文档首页](../index.md)。
