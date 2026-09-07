# Diagram maintenance

This directory contains only cross-module relationship diagrams. Prefer Mermaid for short flows
within a page so that changing one branch does not require synchronizing binary files or large XML.

## File conventions

| File | Purpose |
|---|---|
| `system-overview.drawio` / `.svg` | Seven parts, core dependencies, and the benchmark boundary |
| `context-records.drawio` / `.svg` | Relationships among messages, events, evidence, and persisted artifacts |

`.drawio` is the editable source and `.svg` is the published render. The exported SVG also embeds
diagram data, so draw.io can open it directly; if the two differ, `.drawio` remains authoritative.

## Editing and export

1. Open `.drawio` with draw.io Desktop or diagrams.net;
2. keep node text concise and leave explanations in the adjacent architecture page;
3. enable **Include a copy of my diagram**, use a white background, and crop the export;
4. replace the matching `.svg`, checking that text is not clipped and edges do not cross nodes;
5. commit the source, SVG, and affected architecture documentation together.

The desktop application can also export from the command line:

```bash
drawio --export --format svg --embed-diagram --theme light \
  --embed-svg-fonts false --border 12 \
  --output docs/diagrams/system-overview.svg \
  docs/diagrams/system-overview.drawio
```

Colors express boundaries rather than decoration: blue is orchestration, purple is model adaptation,
green is external execution or verifiable facts, yellow is context/records, and red is the benchmark
or review boundary. Check whether an existing meaning is sufficient before introducing a new color.

Return to the [documentation home](../index.md).
