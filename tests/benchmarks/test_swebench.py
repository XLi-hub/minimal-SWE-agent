import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from mini_agent.config import build_config
from mini_agent.benchmarks.swebench import (
    DATASET_MAPPING,
    PredictionStore,
    SWEbenchRunner,
    _factory_call,
    collect_model_patch,
    filter_instances,
    get_sb_environment,
    get_swebench_docker_image_name,
    load_swebench_dataset,
    process_instance,
)


def _instances():
    return [
        {"instance_id": "repo__one__1", "problem_statement": "fix one"},
        {"instance_id": "repo__two__2", "problem_statement": "fix two"},
        {"instance_id": "other__three__3", "problem_statement": "fix three"},
    ]


def test_dataset_loader_uses_alias_and_split():
    calls = []

    def loader(path, *, split):
        calls.append((path, split))
        return [{"instance_id": "x", "problem_statement": "task"}]

    result = load_swebench_dataset("lite", "test", dataset_loader=loader)

    assert calls == [(DATASET_MAPPING["lite"], "test")]
    assert result == [{"instance_id": "x", "problem_statement": "task"}]


@pytest.mark.parametrize(
    ("alias", "dataset_path"),
    [
        ("full", "SWE-bench/SWE-bench"),
        ("verified", "SWE-bench/SWE-bench_Verified"),
        ("lite", "SWE-bench/SWE-bench_Lite"),
        ("multimodal", "SWE-bench/SWE-bench_Multimodal"),
        ("multilingual", "SWE-bench/SWE-bench_Multilingual"),
    ],
)
def test_official_dataset_aliases_match_current_harness(alias, dataset_path):
    assert DATASET_MAPPING[alias] == dataset_path


def test_swebench_config_uses_extended_execution_budgets():
    config = build_config(["swebench"])

    assert config.agent.context_window == 128000
    assert config.agent.reserve_tokens == 8000
    assert config.agent.keep_last_n_turns == 8
    assert config.agent.max_steps == 400
    assert config.agent.max_time == 2400
    assert config.tools.default_timeout == 120


def test_verified_alias_uses_official_dataset_with_image_column():
    expected_path = "SWE-bench/SWE-bench_Verified"
    calls = []

    def loader(path, *, split):
        calls.append((path, split))
        assert path == expected_path
        return [
            {
                "instance_id": "repo__one__1",
                "problem_statement": "fix one",
                "image": "docker.io/swebench/sweb.eval.x86_64.repo_1776_one_1776_1:latest",
            }
        ]

    result = load_swebench_dataset("verified", "test", dataset_loader=loader)

    assert DATASET_MAPPING["verified"] == expected_path
    assert calls == [(expected_path, "test")]
    assert result[0]["image"].endswith(":latest")


def test_image_name_supports_explicit_and_derived_images():
    assert get_swebench_docker_image_name(
        {
            "instance_id": "x__y__1",
            "image": "current/image:tag",
            "image_name": "legacy/image:tag",
        }
    ) == "current/image:tag"
    assert get_swebench_docker_image_name(
        {"instance_id": "x__y__1", "image_name": "custom/image:tag"}
    ) == "custom/image:tag"
    assert get_swebench_docker_image_name({"instance_id": "x__y__1"}) == (
        "docker.io/swebench/sweb.eval.x86_64.x_1776_y_1776_1:latest"
    )


def test_filter_slice_and_shuffle_are_reproducible():
    instances = _instances()
    first = filter_instances(instances, filter_spec=r"repo__", shuffle=True, seed=7)
    second = filter_instances(instances, filter_spec=r"repo__", shuffle=True, seed=7)

    assert [item["instance_id"] for item in first] == [item["instance_id"] for item in second]
    assert [item["instance_id"] for item in filter_instances(instances, slice_spec="1:3:1")] == [
        "repo__two__2",
        "other__three__3",
    ]
    assert [item["instance_id"] for item in instances] == [
        "repo__one__1",
        "repo__two__2",
        "other__three__3",
    ]


def test_startup_command_renders_instance_and_cleans_on_failure():
    environment = FakeEnvironment()
    calls = []

    def execute(command, timeout=None):
        calls.append(command)
        return {"output": "failed", "returncode": 1, "exception_info": ""}

    environment.execute = execute
    with pytest.raises(RuntimeError, match="startup command"):
        get_sb_environment(
            {"run": {"env_startup_command": "echo {{ instance_id }}"}},
            _instances()[0],
            environment_factory=lambda **kwargs: environment,
        )

    assert calls == ["echo repo__one__1"]
    assert environment.cleaned is True


def test_prediction_store_atomic_updates_and_jsonl(tmp_path):
    store = PredictionStore(tmp_path / "preds.json")
    barrier = threading.Barrier(8)

    def update(index):
        barrier.wait()
        store.update(
            {
                "model_name_or_path": "mock",
                "instance_id": f"task-{index}",
                "model_patch": f"diff-{index}",
            }
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(update, range(8)))

    data = json.loads((tmp_path / "preds.json").read_text())
    assert set(data) == {f"task-{index}" for index in range(8)}
    store.export_jsonl(tmp_path / "preds.jsonl")
    lines = (tmp_path / "preds.jsonl").read_text().splitlines()
    assert len(lines) == 8
    assert {json.loads(line)["instance_id"] for line in lines} == set(data)


class FakeEnvironment:
    def __init__(self):
        self.cleaned = False

    def cleanup(self):
        self.cleaned = True

    def execute(self, command, timeout=None):
        return {"output": "", "returncode": 0, "exception_info": ""}


class FakeAgent:
    def __init__(self, submission="diff --git a/x b/x"):
        self.submission = submission

    def run(self, task):
        return {"exit_status": "submitted", "submission": self.submission, "messages": []}

    def serialize(self):
        message = {"role": "user", "content": "task"}
        return {
            "messages": [message],
            "events": [
                {"sequence": 0, "type": "message", "message": message}
            ],
            "trajectory_format": "mini-agent-0.2",
        }


def test_factory_call_does_not_bind_named_parameter_twice():
    config = object()
    instance = {"instance_id": "repo__one__1"}

    def factory(config, item):
        return config, item

    assert _factory_call(
        factory,
        "model",
        {"config": config, "instance": instance},
    ) == (config, instance)


def test_process_instance_isolates_factories_and_persists_metadata(tmp_path):
    created = {}

    def model_factory(config):
        model = type(
            "Model",
            (),
            {
                "config": type("Config", (), {"model_name": "mock-model"})(),
                "closed": False,
                "close": lambda self: setattr(self, "closed", True),
            },
        )()
        created["model"] = model
        return model

    def environment_factory(instance, image, config):
        created[instance["instance_id"]] = FakeEnvironment()
        assert image.endswith(":latest")
        return created[instance["instance_id"]]

    def agent_factory(model, environment, config):
        return FakeAgent()

    result = process_instance(
        _instances()[0],
        tmp_path,
        model_factory=model_factory,
        environment_factory=environment_factory,
        agent_factory=agent_factory,
    )

    instance_id = _instances()[0]["instance_id"]
    trajectory = tmp_path / instance_id / f"{instance_id}.traj.json"
    assert result["exit_status"] == "submitted"
    assert result["prediction"]["model_patch"].startswith("diff --git")
    assert created[instance_id].cleaned is True
    assert created["model"].closed is True
    data = json.loads(trajectory.read_text())
    event_path = trajectory.with_name(f"{instance_id}.events.jsonl")
    assert data["instance_id"] == instance_id
    assert data["instance"]["problem_statement"] == "fix one"
    assert data["info"]["exit_status"] == "submitted"
    assert data["event_log"]["path"] == event_path.name
    assert json.loads(event_path.read_text())["message"]["content"] == "task"
    assert json.loads((tmp_path / "preds.json").read_text())[instance_id]["model_name_or_path"] == "mock-model"


def test_process_instance_passes_trajectory_path_to_supported_agent(tmp_path):
    seen = {}

    class OutputAwareAgent(FakeAgent):
        def run(self, task, output=None):
            seen["task"] = task
            seen["output"] = output
            return super().run(task)

    instance = _instances()[0]
    result = process_instance(
        instance,
        tmp_path,
        model_factory=lambda **kwargs: object(),
        environment_factory=lambda **kwargs: FakeEnvironment(),
        agent_factory=lambda **kwargs: OutputAwareAgent(),
    )

    expected = tmp_path / instance["instance_id"] / f"{instance['instance_id']}.traj.json"
    assert result["exit_status"] == "submitted"
    assert seen == {"task": "fix one", "output": expected}


def test_trajectory_instance_metadata_excludes_gold_and_evaluation_fields(tmp_path):
    instance = {
        "instance_id": "repo__one__1",
        "repo": "owner/repo",
        "problem_statement": "fix one",
        "base_commit": "abc123",
        "patch": "GOLD PATCH",
        "test_patch": "HIDDEN TEST PATCH",
        "eval_script": "RUN SECRET TESTS",
        "FAIL_TO_PASS": ["secret::failure"],
        "PASS_TO_PASS": ["secret::regression"],
        "hints_text": "answer-shaped hint",
        "custom_private_field": "do not persist",
    }

    result = process_instance(
        instance,
        tmp_path,
        model_factory=lambda **kwargs: object(),
        environment_factory=lambda **kwargs: FakeEnvironment(),
        agent_factory=lambda **kwargs: FakeAgent(),
    )

    assert result["exit_status"] == "submitted"
    path = tmp_path / instance["instance_id"] / f"{instance['instance_id']}.traj.json"
    trajectory_text = path.read_text()
    saved_instance = json.loads(trajectory_text)["instance"]
    assert saved_instance == {
        "instance_id": "repo__one__1",
        "repo": "owner/repo",
        "base_commit": "abc123",
        "problem_statement": "fix one",
    }
    event_text = path.with_name("repo__one__1.events.jsonl").read_text()
    persisted_text = trajectory_text + event_text
    assert "GOLD PATCH" not in persisted_text
    assert "HIDDEN TEST PATCH" not in persisted_text
    assert "RUN SECRET TESTS" not in persisted_text


def test_process_instance_records_factory_error_and_still_cleans(tmp_path):
    env = FakeEnvironment()

    def environment_factory(**kwargs):
        return env

    def agent_factory(**kwargs):
        raise RuntimeError("agent construction failed")

    result = process_instance(
        _instances()[0],
        tmp_path,
        model_factory=lambda **kwargs: object(),
        environment_factory=environment_factory,
        agent_factory=agent_factory,
    )

    assert result["exit_status"] == "RuntimeError"
    assert env.cleaned is True
    instance_id = _instances()[0]["instance_id"]
    trajectory = json.loads(
        (tmp_path / instance_id / f"{instance_id}.traj.json").read_text()
    )
    assert trajectory["info"]["exception_type"] == "RuntimeError"
    assert json.loads((tmp_path / "preds.json").read_text())[instance_id]["model_patch"] == ""


def test_collect_model_patch_falls_back_to_working_tree():
    class Environment:
        def execute(self, command, timeout=None):
            assert command == "git diff --binary --no-ext-diff"
            return {
                "output": "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n",
                "returncode": 0,
                "exception_info": "",
            }

    patch = collect_model_patch("Implemented the fix", Environment())
    assert patch.startswith("diff --git")


def test_runner_resume_and_retry_failed(tmp_path):
    calls = []

    class Agent:
        def run(self, task):
            calls.append(task)
            return {
                "exit_status": "submitted",
                "submission": "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n",
            }

        def serialize(self):
            return {}

    runner = SWEbenchRunner(
        tmp_path,
        model_factory=lambda **kwargs: object(),
        environment_factory=lambda **kwargs: FakeEnvironment(),
        agent_factory=lambda **kwargs: Agent(),
    )
    runner.run(_instances()[:2], workers=2)
    runner.run(_instances()[:2], workers=2)
    assert calls == ["fix one", "fix two"]

    # Mark one prior trajectory as failed, then retry only that instance.
    instance_id = _instances()[0]["instance_id"]
    trajectory_path = tmp_path / instance_id / f"{instance_id}.traj.json"
    trajectory = json.loads(trajectory_path.read_text())
    trajectory["info"]["exit_status"] = "no_tool_calls"
    trajectory_path.write_text(json.dumps(trajectory))
    statuses_path = tmp_path / "statuses.json"
    statuses = json.loads(statuses_path.read_text())
    statuses[instance_id]["exit_status"] = "no_tool_calls"
    statuses_path.write_text(json.dumps(statuses))
    runner.run(_instances()[:2], retry_failed=True)
    assert calls[-1] == "fix one"


def test_runner_keyboard_interrupt_cancels_pending(tmp_path):
    class InterruptingAgent:
        def run(self, task):
            raise KeyboardInterrupt()

    runner = SWEbenchRunner(
        tmp_path,
        model_factory=lambda **kwargs: object(),
        environment_factory=lambda **kwargs: FakeEnvironment(),
        agent_factory=lambda **kwargs: InterruptingAgent(),
    )
    with pytest.raises(KeyboardInterrupt):
        runner.run(_instances(), workers=2)
