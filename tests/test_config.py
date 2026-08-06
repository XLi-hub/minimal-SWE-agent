from src.mini_agent.config import (
    BASH_TOOL,
    DEFAULT_MAX_LINES,
    DEFAULT_MAX_STEPS,
    DEFAULT_TIMEOUT,
    SUBMIT_TOOL,
    SYSTEM_PROMPT,
)


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


def test_bash_tool_timeout_is_integer():
    """timeout 参数类型应为 integer。"""
    props = BASH_TOOL["function"]["parameters"]["properties"]
    assert "timeout" in props
    assert props["timeout"]["type"] == "integer"


def test_bash_tool_timeout_is_not_required():
    """timeout 是可选参数。"""
    required = BASH_TOOL["function"]["parameters"]["required"]
    assert "timeout" not in required


# --- system prompt ---

def test_system_prompt_mentions_bash_tool():
    assert "bash" in SYSTEM_PROMPT.lower()


def test_system_prompt_mentions_submit():
    assert "submit" in SYSTEM_PROMPT.lower()


# --- submit tool ---


def test_submit_tool_has_correct_type():
    assert SUBMIT_TOOL["type"] == "function"


def test_submit_tool_has_name():
    assert SUBMIT_TOOL["function"]["name"] == "submit"


def test_submit_tool_has_output_param():
    props = SUBMIT_TOOL["function"]["parameters"]["properties"]
    assert "output" in props
    assert props["output"]["type"] == "string"


def test_submit_tool_requires_output():
    assert SUBMIT_TOOL["function"]["parameters"]["required"] == ["output"]


# --- defaults ---

def test_default_max_lines_is_positive():
    assert DEFAULT_MAX_LINES > 0


def test_default_max_steps_is_positive():
    assert DEFAULT_MAX_STEPS > 0


def test_default_timeout_is_positive():
    assert DEFAULT_TIMEOUT > 0
