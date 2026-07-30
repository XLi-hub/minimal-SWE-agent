from src.mini_agent.parser import parse_action


def test_extracts_triple_backtick_action():
    output = """Let me list the files.

```bash-action
ls -la
```
"""
    assert parse_action(output) == "ls -la"


def test_empty_when_no_action():
    assert parse_action("Hello, how can I help you today?") == ""


def test_multiline_command():
    output = """```bash-action
cd /tmp && ls -la && echo done
```
"""
    assert parse_action(output) == "cd /tmp && ls -la && echo done"


def test_returns_first_match_when_multiple_blocks():
    output = """```bash-action
first-command
```
Some text
```bash-action
second-command
```
"""
    assert parse_action(output) == "first-command"


def test_no_newline_before_close():
    # 边界：命令后没有换行直接 ```
    output = "```bash-action\npwd```"
    result = parse_action(output)
    assert result == "" or result == "pwd"
