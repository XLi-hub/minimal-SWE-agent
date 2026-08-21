"""Control-flow exceptions that end the agent loop.

These are not error conditions — they are the mechanism by which the
``while True`` loop in :meth:`Agent.run` is told how to terminate.  Each
subclass maps to a concrete ``exit_status`` string (the exact values the
tests and CLI depend on) and carries an optional ``submission``.
"""


class AgentExit(Exception):
    """Base class for signals that end the agent loop."""

    def __init__(self, exit_status: str, submission: str = ""):
        self.exit_status = exit_status
        self.submission = submission
        super().__init__(exit_status)


class Submitted(AgentExit):
    """The model called the ``submit`` tool — the run completed with an answer."""

    def __init__(self, submission: str = ""):
        super().__init__("submitted", submission)


class NoToolCalls(AgentExit):
    """The model returned a plain-text reply with no tool calls."""

    def __init__(self):
        super().__init__("no_tool_calls")


class MaxSteps(AgentExit):
    """The step budget was exhausted."""

    def __init__(self):
        super().__init__("max_steps")


class MaxTime(AgentExit):
    """The wall-clock deadline was exceeded."""

    def __init__(self):
        super().__init__("max_time")


class CostLimit(AgentExit):
    """The accumulated cost exceeded the configured limit."""

    def __init__(self):
        super().__init__("cost_limit")
