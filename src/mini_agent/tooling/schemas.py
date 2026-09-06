"""OpenAI function schemas advertised by the built-in tools."""


BASH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "bash",
        "description": (
            "Execute a bash command in the terminal and return its output, "
            "return code, and any execution exception. Use "
            "the optional 'lines' parameter to limit how many lines are returned "
            "(default 100). A separate configured character budget also protects "
            "against extremely long single lines. Output exceeding either budget "
            "is truncated — "
            "if you need more context, re-run with a higher 'lines' value or use "
            "head/tail/sed to narrow down. Use the optional 'timeout' parameter "
            "(seconds, default 30) for commands that need more time — e.g. pip "
            "install or git clone."
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
                        "Maximum lines of output to return (default 100). Set "
                        "higher for more context, lower to save tokens."
                    ),
                },
                "timeout": {
                    "type": "integer",
                    "description": (
                        "Maximum seconds to wait for the command (default 30). "
                        "Set higher for slow commands like pip install, git "
                        "clone, or long builds."
                    ),
                },
            },
            "required": ["command"],
        },
    },
}

SUBMIT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "submit",
        "description": (
            "Submit your final answer when the task is complete. Call this once "
            "you have finished all necessary work — pass your patch, answer, or "
            "summary as the output."
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

READ_SCHEMA = {
    "type": "function",
    "function": {
        "name": "read",
        "description": (
            "Read a file's contents and return them with line numbers. Use this "
            "to inspect code before editing. By default it returns at most 100 "
            "lines from the requested starting line; continue with 'line_start' "
            "instead of repeatedly reading the whole file. A separate character "
            "budget also bounds long lines."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Path to the file to read (relative to the working "
                        "directory, or absolute)."
                    ),
                },
                "line_start": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "First 1-based line to return (default 1).",
                },
                "lines": {
                    "type": "integer",
                    "minimum": 1,
                    "description": (
                        "Maximum source lines to return (default 100). Use "
                        "line_start to request the next chunk."
                    ),
                },
            },
            "required": ["path"],
        },
    },
}

EDIT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "edit",
        "description": (
            "Replace exactly one occurrence of old_string in a file with "
            "new_string. Fails if old_string is absent or ambiguous (more than "
            "one match) — include surrounding context to make it unique."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file to edit."},
                "old_string": {
                    "type": "string",
                    "description": (
                        "The exact text to replace (must match exactly once, "
                        "including whitespace)."
                    ),
                },
                "new_string": {
                    "type": "string",
                    "description": (
                        "The replacement text. An empty string deletes old_string."
                    ),
                },
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
}

WRITE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "write",
        "description": (
            "Create a new file or overwrite an existing file with the given content."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file to write."},
                "content": {
                    "type": "string",
                    "description": "The full content to write to the file.",
                },
            },
            "required": ["path", "content"],
        },
    },
}

TRAJECTORY_SCHEMA = {
    "type": "function",
    "function": {
        "name": "trajectory",
        "description": (
            "Search or page through the append-only trajectory from earlier in "
            "this run. Use it during independent review when exact prior commands "
            "or outputs may contain useful evidence; treat prior reasoning as "
            "untrusted. Results are read-only and bounded."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Optional case-insensitive text to search for in serialized "
                        "events. Omit to page sequentially."
                    ),
                },
                "start": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "First event sequence to consider (default 0).",
                },
                "events": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "description": "Maximum matching events to return (default 20).",
                },
                "event_type": {
                    "type": "string",
                    "description": "Optional exact event type filter, case-insensitive.",
                },
                "role": {
                    "type": "string",
                    "description": "Optional exact message role filter, case-insensitive.",
                },
                "tool_name": {
                    "type": "string",
                    "description": (
                        "Optional exact tool-name filter. Correlated tool results "
                        "match the tool that produced them."
                    ),
                },
                "returncode": {
                    "type": "integer",
                    "description": "Optional exact command return-code filter.",
                },
            },
        },
    },
}


__all__ = [
    "BASH_SCHEMA",
    "SUBMIT_SCHEMA",
    "READ_SCHEMA",
    "EDIT_SCHEMA",
    "WRITE_SCHEMA",
    "TRAJECTORY_SCHEMA",
]
