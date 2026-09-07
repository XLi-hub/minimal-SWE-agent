"""Shared fixtures and builders for tool-layer tests."""

import json
from unittest.mock import MagicMock


def _tc(id_: str, name: str, arguments: dict):
    tc = MagicMock()
    tc.id = id_
    tc.function.name = name
    tc.function.arguments = json.dumps(arguments)
    return tc


class FakeEnv:
    """Minimal in-memory environment recording read_file/write_file calls."""

    def __init__(self, files=None):
        self.files = dict(files or {})
        self.read_calls: list[str] = []
        self.write_calls: list[tuple[str, str]] = []

    def read_file(self, path):
        self.read_calls.append(path)
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    def write_file(self, path, content):
        self.write_calls.append((path, content))
        self.files[path] = content

    def execute(self, command, **kwargs):
        return f"output of {command}"
