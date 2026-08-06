from src.mini_agent.parser import BASH_TOOL


def test_bash_tool_has_correct_type():
    assert BASH_TOOL["type"] == "function"


def test_bash_tool_has_name():
    assert BASH_TOOL["function"]["name"] == "bash"


def test_bash_tool_has_description():
    assert len(BASH_TOOL["function"]["description"]) > 0


def test_bash_tool_requires_command_parameter():
    assert "command" in BASH_TOOL["function"]["parameters"]["required"]


def test_bash_tool_command_is_string():
    props = BASH_TOOL["function"]["parameters"]["properties"]
    assert props["command"]["type"] == "string"
