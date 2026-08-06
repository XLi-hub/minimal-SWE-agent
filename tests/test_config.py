from src.mini_agent.config import BASH_TOOL, DEFAULT_MAX_LINES, SYSTEM_PROMPT


# --- tool schema ---

def test_bash_tool_has_correct_type():
    assert BASH_TOOL["type"] == "function"


def test_bash_tool_has_name():
    assert BASH_TOOL["function"]["name"] == "bash"


def test_bash_tool_has_description():
    assert len(BASH_TOOL["function"]["description"]) > 0


def test_bash_tool_requires_only_command():
    assert BASH_TOOL["function"]["parameters"]["required"] == ["command"]


def test_bash_tool_command_is_string():
    props = BASH_TOOL["function"]["parameters"]["properties"]
    assert props["command"]["type"] == "string"


def test_bash_tool_lines_is_integer():
    props = BASH_TOOL["function"]["parameters"]["properties"]
    assert "lines" in props
    assert props["lines"]["type"] == "integer"


def test_bash_tool_lines_is_not_required():
    """lines 是可选参数。"""
    required = BASH_TOOL["function"]["parameters"]["required"]
    assert "lines" not in required


# --- system prompt ---

def test_system_prompt_mentions_bash_tool():
    assert "bash" in SYSTEM_PROMPT.lower()


def test_system_prompt_mentions_no_tool_call_for_completion():
    assert "without calling any tools" in SYSTEM_PROMPT


# --- defaults ---

def test_default_max_lines_is_positive():
    assert DEFAULT_MAX_LINES > 0
