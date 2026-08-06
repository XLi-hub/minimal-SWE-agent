"""Agent configuration — system prompt, tool schemas, and truncation settings."""

# ---------------------------------------------------------------------------
# system prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You are a helpful assistant. "
    "Use the bash tool to run commands in the terminal. "
    "When your task is complete, call the submit tool with your "
    "final answer, patch, or summary of what was done."
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
