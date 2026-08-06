"""Agent configuration — system prompt, tool schemas, and truncation settings."""

# ---------------------------------------------------------------------------
# system prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You are a helpful assistant. "
    "Use the bash tool to run commands in the terminal. "
    "When your task is complete, reply with a text message "
    "without calling any tools."
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
            "'lines' value or use head/tail/sed to narrow down."
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
            },
            "required": ["command"],
        },
    },
}

# ---------------------------------------------------------------------------
# truncation
# ---------------------------------------------------------------------------

DEFAULT_MAX_LINES = 100
"""Default line limit when the model does not specify ``lines``."""
