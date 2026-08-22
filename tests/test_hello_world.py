"""Offline integration test for the Python-library hello-world example."""

import copy
import json
from types import SimpleNamespace
from unittest.mock import patch

from examples import hello_world


def _tool_call(id_: str, name: str, arguments: dict) -> SimpleNamespace:
    return SimpleNamespace(
        id=id_,
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )


def _response(content: str, tool_call: SimpleNamespace) -> SimpleNamespace:
    message = SimpleNamespace(content=content, tool_calls=[tool_call])
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=None,
    )


class DeterministicModel:
    """Two fixed model turns: execute Python, then submit its output."""

    def __init__(self) -> None:
        self.responses = iter([
            _response(
                "I will run the requested command.",
                _tool_call(
                    "bash-1",
                    "bash",
                    {"command": "python -c 'print(\"Hello, world!\")'"},
                ),
            ),
            _response(
                "The output matches.",
                _tool_call("submit-1", "submit", {"output": "Hello, world!"}),
            ),
        ])
        self.calls: list[tuple[list[dict], list[dict] | None]] = []

    def query(self, messages, tools=None):
        self.calls.append((copy.deepcopy(messages), copy.deepcopy(tools)))
        return next(self.responses)


def test_hello_world_uses_python_library_end_to_end(capsys, monkeypatch, tmp_path):
    """Compose the real Agent and LocalEnvironment without calling an API."""
    model = DeterministicModel()
    monkeypatch.chdir(tmp_path)

    with patch.object(hello_world, "Model", return_value=model):
        result = hello_world.main()

    assert result["exit_status"] == "submitted"
    assert result["submission"] == "Hello, world!"
    assert len(model.calls) == 2

    advertised_tools = {
        tool["function"]["name"] for tool in model.calls[0][1] or []
    }
    assert {"bash", "submit"} <= advertised_tools

    second_turn_messages = model.calls[1][0]
    tool_outputs = [
        message["content"]
        for message in second_turn_messages
        if message["role"] == "tool"
    ]
    assert tool_outputs == ["Hello, world!\n"]
    assert list(tmp_path.iterdir()) == []
    assert capsys.readouterr().out.rstrip().endswith("Hello, world!")
