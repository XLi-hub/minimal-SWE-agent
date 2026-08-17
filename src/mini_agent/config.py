"""Agent configuration — system prompt, tool schemas, and truncation settings."""

# ---------------------------------------------------------------------------
# system prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You are an autonomous software engineer solving tasks in a Linux "
    "environment. You have two tools:\n"
    "- `bash`: execute a shell command and see its output.\n"
    "- `submit`: call this when your task is complete.\n\n"
    "## Core rules\n\n"
    "1. Think before you act — reason about what you need to do and why, "
    "then execute ONE bash command at a time. Never batch multiple "
    "commands in a single call.\n"
    "2. Read before you edit — use cat, grep, ls, or find to understand "
    "the code before making changes. Guessing leads to wrong fixes.\n"
    "3. Make the smallest change that solves the problem. Avoid "
    "unrelated refactoring, style changes, or adding unnecessary "
    "features.\n"
    "4. If a command fails, read the error message carefully and adapt. "
    "Do not blindly retry the same thing.\n"
    "5. Verify your work — run tests, check outputs, or use git diff "
    "to review your changes before calling submit.\n"
    "6. When you are stuck, explore more rather than guessing. Use ls, "
    "find, grep, and git log to gather context.\n\n"
    "## Output limits\n\n"
    "Command output is truncated to 100 lines by default. If you need "
    "more context, pass a higher `lines` value or use head/tail/sed "
    "to narrow down. For long-running commands like pip install or "
    "git clone, pass a higher `timeout` value (default 30s)."
)

# ---------------------------------------------------------------------------
# instance template — wraps the user's task with a structured workflow
# ---------------------------------------------------------------------------

INSTANCE_TEMPLATE = (
    "## Task\n{task}\n\n"
    "## Workflow\n"
    "Follow these steps to complete the task:\n\n"
    "1. **Explore** — understand the repository structure. Find and read "
    "the files relevant to the task.\n"
    "2. **Diagnose** — identify the root cause. If it is a bug, create a "
    "minimal script to reproduce it before fixing.\n"
    "3. **Fix** — make the necessary changes. Edit source files to "
    "resolve the issue.\n"
    "4. **Verify** — confirm the fix works. Re-run your reproduction "
    "script, run existing tests, or check the output manually.\n"
    "5. **Submit** — call the submit tool with your patch (from git diff), "
    "the final answer, or a summary of what was done.\n\n"
    "Start now — begin with step 1 (Explore)."
)

# ---------------------------------------------------------------------------
# tool definitions
# ---------------------------------------------------------------------------

BASH_TOOL = {
    "type": "function",
    "function": {
        "name": "bash",
        "description": (
            "Execute a bash command in the terminal and return its output. "
            "Use the optional 'lines' parameter to limit how many lines are "
            "returned (default 100). The output is truncated when it exceeds "
            "this limit — if you need more context, re-run with a higher "
            "'lines' value or use head/tail/sed to narrow down. "
            "Use the optional 'timeout' parameter (seconds, default 30) "
            "for commands that need more time — e.g. pip install or git clone."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The bash command to execute.",
                },
                "lines": {
                    "type": "integer",
                    "description": (
                        "Maximum lines of output to return (default 100). "
                        "Set higher for more context, lower to save tokens."
                    ),
                },
                "timeout": {
                    "type": "integer",
                    "description": (
                        "Maximum seconds to wait for the command (default 30). "
                        "Set higher for slow commands like pip install, "
                        "git clone, or long builds."
                    ),
                },
            },
            "required": ["command"],
        },
    },
}

SUBMIT_TOOL = {
    "type": "function",
    "function": {
        "name": "submit",
        "description": (
            "Submit your final answer when the task is complete. "
            "Call this once you have finished all necessary work — "
            "pass your patch, answer, or summary as the output."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "output": {
                    "type": "string",
                    "description": "Final answer, patch, or summary of what was done.",
                },
            },
            "required": ["output"],
        },
    },
}

# ---------------------------------------------------------------------------
# truncation
# ---------------------------------------------------------------------------

DEFAULT_MAX_LINES = 100
"""Default line limit when the model does not specify ``lines``."""

DEFAULT_TIMEOUT = 30
"""Default per-command timeout in seconds when the model does not specify ``timeout``."""

DEFAULT_MAX_STEPS = 250
"""Default maximum tool-calling iterations before the agent stops."""

DEFAULT_MAX_TIME = 1800
"""Default maximum wall-clock time (seconds) for the entire agent run."""

# ---------------------------------------------------------------------------
# context compression
# ---------------------------------------------------------------------------

CONTEXT_WINDOW = 64000
"""Estimated context window (tokens) of the model (``deepseek-chat``)."""

COMPRESS_THRESHOLD = 0.8
"""Compress when history reaches this fraction of ``context_window - reserve``."""

RESERVE_TOKENS = 2000
"""Tokens reserved for the model's next reply (so compression leaves headroom)."""

KEEP_LAST_N_TURNS = 4
"""Number of most-recent round-trip units kept verbatim after compression."""

SUMMARY_MARKER = "[CONTEXT SUMMARY]"
"""Prefix marking a message as the compressed summary of earlier history."""

SUMMARY_PROMPT = (
    "Summarize the agent's conversation history so it can continue without "
    "the full transcript. Be concise and factual; preserve exact file paths, "
    "command names, error messages, and test results. Use these headers:\n\n"
    "## Task\n## Files Changed\n## Key Decisions\n## Errors / Test Results\n"
    "## Current State\n## Next Steps\n\n"
    "### Existing summary (may be empty)\n{existing_summary}\n\n"
    "### New history since the last summary\n{new_lines}\n"
)
"""Prompt template for folding new history lines into a running summary."""

# ---------------------------------------------------------------------------
# cost tracking — DeepSeek `deepseek-chat` (deepseek-v4-flash) USD 单价/百万 token
# 来源: https://api-docs.deepseek.com/zh-cn/quick_start/pricing/
# 注: 2026/08/17 起 DeepSeek 改为峰谷计价，此处的 2026/07 固定价仅作默认值，可按需改。
# ---------------------------------------------------------------------------

PRICE_INPUT_PER_1M = 0.14
"""Input (cache miss) price in USD per 1M tokens."""

PRICE_INPUT_CACHE_HIT_PER_1M = 0.0028
"""Input (cache hit) price in USD per 1M tokens."""

PRICE_OUTPUT_PER_1M = 0.28
"""Output price in USD per 1M tokens."""

DEFAULT_COST_LIMIT = 3.0
"""Stop the agent once accumulated cost exceeds this USD value.

``0`` or ``None`` disables the limit.
"""
