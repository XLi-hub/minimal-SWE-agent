"""Tests for the public CLI entry points."""

import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from mini_agent import cli
from mini_agent.config import UNSET


def test_parse_args_accepts_runtime_overrides():
    args = cli._parse_args(
        [
            "--config", "custom.yaml",
            "-c", "agent.max_steps=50",
            "--env", "docker",
            "--image", "ubuntu:22.04",
            "--cwd", "/workspace",
            "--max-steps", "12",
            "--max-time", "30.5",
            "--task", "fix the bug",
            "--output", "run.traj.json",
            "--cost-limit", "1.5",
            "--context-window", "2000",
            "--keep-last-n-turns", "2",
        ]
    )

    assert args.config == ["custom.yaml", "agent.max_steps=50"]
    assert args.env == "docker"
    assert args.image == "ubuntu:22.04"
    assert args.cwd == "/workspace"
    assert args.max_steps == 12
    assert args.max_time == 30.5
    assert args.task == "fix the bug"
    assert args.output == "run.traj.json"
    assert args.cost_limit == 1.5
    assert args.context_window == 2000
    assert args.keep_last_n_turns == 2


def test_parse_args_leaves_optional_values_unset():
    args = cli._parse_args([])

    assert args.config == []
    assert args.task is None
    assert args.max_steps is None
    assert args.max_time is None
    assert args.cost_limit is None


def test_u_distinguishes_missing_from_explicit_value():
    assert cli._u(None) is UNSET
    assert cli._u(0) == 0
    assert cli._u("docker") == "docker"


def test_main_builds_components_and_runs_task():
    config = SimpleNamespace(
        model=object(),
        environment=SimpleNamespace(type="docker"),
    )
    env = MagicMock()
    model = MagicMock()
    agent = MagicMock()
    agent.run.return_value = {
        "exit_status": "submitted",
        "submission": "patch",
    }

    with (
        patch.object(cli, "build_config", return_value=config) as build_config,
        patch.object(cli, "get_environment", return_value=env) as get_environment,
        patch.object(cli, "Model", return_value=model) as model_factory,
        patch.object(cli, "Agent", return_value=agent) as agent_factory,
    ):
        exit_code = cli.main(
            [
                "--env", "docker",
                "--task", "fix it",
                "--output", "run.json",
                "--max-steps", "12",
                "--cost-limit", "1.5",
            ]
        )

    assert exit_code == 0
    build_config.assert_called_once()
    overrides = build_config.call_args.kwargs["cli_overrides"]
    assert overrides["agent"]["max_steps"] == 12
    assert overrides["agent"]["cost_limit"] == 1.5
    assert overrides["agent"]["max_time"] is UNSET
    assert overrides["environment"]["type"] == "docker"
    assert overrides["environment"]["image"] is UNSET
    get_environment.assert_called_once_with("docker", config=config.environment)
    model_factory.assert_called_once_with(config.model)
    agent_factory.assert_called_once_with(model, env, config=config)
    agent.run.assert_called_once_with("fix it", output="run.json")
    model.close.assert_called_once_with()
    env.cleanup.assert_called_once_with()


def test_main_reads_task_interactively_when_omitted():
    config = SimpleNamespace(
        model=object(),
        environment=SimpleNamespace(type="local"),
    )
    agent = MagicMock()
    agent.run.return_value = {"exit_status": "no_tool_calls", "submission": ""}

    with (
        patch.object(cli, "build_config", return_value=config),
        patch.object(cli, "get_environment"),
        patch.object(cli, "Model"),
        patch.object(cli, "Agent", return_value=agent),
        patch("builtins.input", return_value="interactive task"),
    ):
        assert cli.main([]) == cli.EXIT_CODES["no_tool_calls"]

    agent.run.assert_called_once_with("interactive task", output=None)


def test_explicit_empty_task_does_not_prompt():
    config = SimpleNamespace(
        model=object(),
        environment=SimpleNamespace(type="local"),
    )
    agent = MagicMock()
    agent.run.return_value = {"exit_status": "submitted", "submission": ""}
    prompt = MagicMock(side_effect=AssertionError("empty task prompted"))

    with (
        patch.object(cli, "build_config", return_value=config),
        patch.object(cli, "get_environment"),
        patch.object(cli, "Model"),
        patch.object(cli, "Agent", return_value=agent),
        patch("builtins.input", prompt),
    ):
        assert cli.main(["--task", ""]) == 0

    agent.run.assert_called_once_with("", output=None)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("submitted", 0),
        ("no_tool_calls", 2),
        ("max_steps", 3),
        ("max_time", 4),
        ("cost_limit", 5),
        ("error", 1),
        ("interrupted", 130),
    ],
)
def test_main_maps_agent_status_to_process_exit_code(status, expected):
    config = SimpleNamespace(
        model=object(),
        environment=SimpleNamespace(type="local"),
    )
    env = MagicMock()
    model = MagicMock()
    agent = MagicMock()
    agent.run.return_value = {"exit_status": status, "submission": ""}

    with (
        patch.object(cli, "build_config", return_value=config),
        patch.object(cli, "get_environment", return_value=env),
        patch.object(cli, "Model", return_value=model),
        patch.object(cli, "Agent", return_value=agent),
    ):
        assert cli.main(["--task", "task"]) == expected

    model.close.assert_called_once_with()
    env.cleanup.assert_called_once_with()


def test_main_cleanup_does_not_mask_run_exception():
    config = SimpleNamespace(
        model=object(),
        environment=SimpleNamespace(type="local"),
    )
    env = MagicMock()
    env.cleanup.side_effect = RuntimeError("environment cleanup failed")
    model = MagicMock()
    model.close.side_effect = RuntimeError("model close failed")
    agent = MagicMock()
    agent.run.side_effect = ValueError("run failed")

    with (
        patch.object(cli, "build_config", return_value=config),
        patch.object(cli, "get_environment", return_value=env),
        patch.object(cli, "Model", return_value=model),
        patch.object(cli, "Agent", return_value=agent),
        pytest.raises(ValueError, match="run failed"),
    ):
        cli.main(["--task", "task"])

    model.close.assert_called_once_with()
    env.cleanup.assert_called_once_with()


def test_main_releases_environment_when_model_construction_fails():
    config = SimpleNamespace(
        model=object(),
        environment=SimpleNamespace(type="local"),
    )
    env = MagicMock()
    model_error = RuntimeError("model construction failed")

    with (
        patch.object(cli, "build_config", return_value=config),
        patch.object(cli, "get_environment", return_value=env),
        patch.object(cli, "Model", side_effect=model_error),
        pytest.raises(RuntimeError, match="model construction failed"),
    ):
        cli.main(["--task", "task"])

    env.cleanup.assert_called_once_with()


def test_module_entrypoint_exposes_version():
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        ["src", env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)

    result = subprocess.run(
        [sys.executable, "-m", "mini_agent", "--version"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.strip().endswith(" 0.1.0")


def test_module_entrypoint_exposes_help():
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        ["src", env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)

    result = subprocess.run(
        [sys.executable, "-m", "mini_agent", "--help"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert "usage:" in result.stdout
    assert "--task" in result.stdout
