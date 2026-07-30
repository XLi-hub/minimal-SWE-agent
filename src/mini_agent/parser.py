"""动作解析模块 — 从 LM 输出中提取 bash 命令."""

import re


def parse_action(lm_output: str) -> str:
    """Extract the first ```bash-action ... ``` block from LM output.

    Returns the command string, or empty string if no match.
    """
    matches = re.findall(
        r"```bash-action\s*\n(.*?)\n```",
        lm_output,
        re.DOTALL,
    )
    return matches[0].strip() if matches else ""
