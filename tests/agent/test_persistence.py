import json
from unittest.mock import MagicMock

from mini_agent.agent import Agent

from ._helpers import _make_response, _make_tool_call


# ---------------------------------------------------------------------------
# trajectory saving — serialize / save
# ---------------------------------------------------------------------------


def _submitting_model():
    """A model that answers with a single submit tool call."""
    model = MagicMock()
    model.query.side_effect = [
        _make_response(
            content="Done.",
            tool_calls=[
                _make_tool_call("s1", "submit", {"output": "final answer"}),
            ],
        ),
    ]
    return model


class TestSerialize:
    """Tests for Agent.serialize()."""

    def test_serialize_returns_structured_dict(self):
        """serialize() 返回带 info/messages/trajectory_format 的 dict。"""
        agent = Agent(_submitting_model(), MagicMock())
        agent.run("do it")

        data = agent.serialize()

        assert data["trajectory_format"] == "mini-agent-0.2"
        assert data["info"]["exit_status"] == "submitted"
        assert data["info"]["submission"] == "final answer"
        assert data["info"]["model_stats"]["api_calls"] == 1
        assert data["info"]["config"]["agent_type"].endswith("Agent")
        assert data["info"]["config"]["model"]["model_name"] == "gpt-4o-mini"
        assert data["info"]["config"]["environment"]["type"] == "local"
        assert data["info"]["mini_version"] == "0.1.0"
        # messages must be the same list the agent used during the run
        assert data["messages"] == agent.messages
        assert data["messages"][0]["role"] == "system"
        assert [event["sequence"] for event in data["events"]] == list(
            range(len(data["events"]))
        )
        assert [event["type"] for event in data["events"]] == [
            "message", "message", "message", "message"
        ]

    def test_serialize_before_run_is_empty(self):
        """未运行前 serialize() 返回空轨迹。"""
        agent = Agent(MagicMock(), MagicMock())

        data = agent.serialize()

        assert data["messages"] == []
        assert data["events"] == []
        assert data["info"]["exit_status"] == ""
        assert data["info"]["submission"] == ""
        assert data["info"]["model_stats"]["api_calls"] == 0

    def test_serialize_is_json_serializable(self):
        """serialize() 的输出可直接 json.dumps。"""
        agent = Agent(_submitting_model(), MagicMock())
        agent.run("do it")

        json.dumps(agent.serialize())


class TestSave:
    """Tests for Agent.save()."""

    def test_save_writes_traj_json(self, tmp_path):
        """save() 写出合法的 .traj.json 文件，并返回相同数据。"""
        agent = Agent(_submitting_model(), MagicMock())
        agent.run("do it")

        path = tmp_path / "out" / "run.traj.json"
        data = agent.save(path)

        assert path.exists()
        loaded = json.loads(path.read_text())
        assert loaded == data
        assert loaded["info"]["exit_status"] == "submitted"
        assert "events" not in loaded
        event_path = path.with_name("run.events.jsonl")
        events = [json.loads(line) for line in event_path.read_text().splitlines()]
        assert loaded["event_log"]["path"] == event_path.name
        assert loaded["event_log"]["event_count"] == len(events) == 4

    def test_save_none_returns_data_without_writing(self, tmp_path):
        """save(None) 不写文件，只返回序列化数据。"""
        agent = Agent(_submitting_model(), MagicMock())
        agent.run("do it")

        data = agent.save(None)

        assert data["info"]["exit_status"] == "submitted"
        assert list(tmp_path.iterdir()) == []

    def test_save_creates_parent_directories(self, tmp_path):
        """save() 自动创建父目录。"""
        agent = Agent(_submitting_model(), MagicMock())
        agent.run("do it")

        path = tmp_path / "a" / "b" / "c.traj.json"
        agent.save(path)

        assert path.exists()
        assert (path.parent / "c.events.jsonl").exists()


class TestRunAutoSave:
    """Tests that run(..., output=...) saves the trajectory automatically."""

    def test_run_saves_when_output_given(self, tmp_path):
        """run(output=...) 结束后自动写出轨迹文件。"""
        agent = Agent(_submitting_model(), MagicMock())

        path = tmp_path / "run.traj.json"
        result = agent.run("do it", output=path)

        assert path.exists()
        loaded = json.loads(path.read_text())
        assert loaded["info"]["exit_status"] == result["exit_status"]
        assert loaded["info"]["submission"] == result["submission"]
        assert loaded["messages"] == result["messages"]
        event_path = tmp_path / "run.events.jsonl"
        assert event_path.exists()
        assert loaded["event_log"]["event_count"] == len(
            event_path.read_text().splitlines()
        )

    def test_run_saves_on_max_steps(self, tmp_path):
        """达到 max_steps 退出时也应保存轨迹。"""
        model = MagicMock()
        model.query.return_value = _make_response(
            content="Running.",
            tool_calls=[_make_tool_call("c1", "bash", {"command": "ls"})],
        )
        env = MagicMock()
        env.execute.return_value = "ok"

        agent = Agent(model, env)
        path = tmp_path / "partial.traj.json"
        result = agent.run("infinite", max_steps=3, output=path)

        assert result["exit_status"] == "max_steps"
        assert path.exists()
        assert json.loads(path.read_text())["info"]["exit_status"] == "max_steps"

    def test_event_sidecar_is_journalled_before_run_finishes(self, tmp_path):
        """工具执行期间已能看到先前事件，而不是只在 finally 一次性写入。"""
        model = MagicMock()
        model.query.side_effect = [
            _make_response(
                content="Run.",
                tool_calls=[_make_tool_call("c1", "bash", {"command": "echo ok"})],
            ),
            _make_response(
                content="Done.",
                tool_calls=[_make_tool_call("s1", "submit", {"output": "done"})],
            ),
        ]
        event_path = tmp_path / "live.events.jsonl"
        observed_sequences = []

        def execute(command, timeout=None):
            observed_sequences.extend(
                json.loads(line)["sequence"]
                for line in event_path.read_text().splitlines()
            )
            return "ok"

        env = MagicMock()
        env.execute.side_effect = execute
        result = Agent(model, env).run("task", output=tmp_path / "live.traj.json")

        assert result["exit_status"] == "submitted"
        assert observed_sequences == [0, 1, 2]

    def test_run_without_output_does_not_save(self, tmp_path):
        """run(output=None) 不写文件，但 self 状态已同步可 serialize。"""
        agent = Agent(_submitting_model(), MagicMock())
        result = agent.run("do it")

        assert list(tmp_path.iterdir()) == []
        assert agent.serialize()["info"]["exit_status"] == result["exit_status"]
