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
            "for commands that need more time — e.g. pip install or git clone. "
            "For very long tasks (training, large builds), run the command "
            "in the background: 'nohup CMD &> /tmp/output.log & echo PID: $!'. "
            "Then check progress with 'tail /tmp/output.log' or 'ps PID'. "
            "Check every 1-3 minutes for fast tasks, every 5-10 minutes for "
            "slow ones. Use 'grep' to look for completion markers (accuracy, "
            "done, error) rather than reading the full log each time. "
            "When the task is done, read the final results with cat/tail."
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
