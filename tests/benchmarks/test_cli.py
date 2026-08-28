from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from mini_agent.benchmarks import cli
from mini_agent.config import build_config


def test_parse_args_supports_batch_selection():
    args = cli._parse_args(
        [
            "--subset", "verified",
            "--split", "test",
            "--slice", "0:5",
            "--filter", "django__",
            "--workers", "4",
            "--retry-failed",
        ]
    )
    assert args.subset == "verified"
    assert args.split == "test"
    assert args.slice_spec == "0:5"
    assert args.filter == "django__"
    assert args.workers == 4
    assert args.retry_failed is True


def test_select_instance_accepts_id_and_sorted_index():
    instances = [
        {"instance_id": "z__repo__2"},
        {"instance_id": "a__repo__1"},
    ]
    assert cli._select_instance(instances, "z__repo__2")[0]["instance_id"] == "z__repo__2"
    assert cli._select_instance(instances, "0")[0]["instance_id"] == "a__repo__1"
    with pytest.raises(ValueError, match="Unknown"):
        cli._select_instance(instances, "missing")


def test_warns_when_cost_limit_has_no_prices(capsys):
    config = build_config([])

    cli._warn_if_cost_limit_is_unenforced(config)

    assert "cost limit cannot stop this run" in capsys.readouterr().err


@pytest.mark.parametrize(
    "overrides",
    [
        {"agent": {"cost_limit": 0}},
        {"agent": {"cost_limit": None}},
        {"cost": {"price_output_per_1m": 1.0}},
    ],
)
def test_cost_limit_warning_is_silent_when_disabled_or_priced(overrides, capsys):
    config = build_config([], cli_overrides=overrides)

    cli._warn_if_cost_limit_is_unenforced(config)

    assert capsys.readouterr().err == ""


def test_main_builds_benchmark_config_and_runs(tmp_path, capsys):
    runner = MagicMock()
    runner.load_instances.return_value = [
        {"instance_id": "repo__one__1", "problem_statement": "fix"}
    ]
    runner.run.return_value = [
        {"instance_id": "repo__one__1", "exit_status": "submitted"}
    ]
    runner.predictions.path = tmp_path / "preds.json"
    config = SimpleNamespace()

    with (
        patch.object(cli, "build_config", return_value=config) as build_config,
        patch.object(cli, "SWEbenchRunner", return_value=runner) as runner_class,
    ):
        code = cli.main(
            [
                "--instance", "repo__one__1",
                "--output", str(tmp_path),
                "--model", "test-model",
            ]
        )

    assert code == 0
    assert build_config.call_args.args[0][0] == "benchmarks/swebench"
    runner_class.assert_called_once()
    runner.run.assert_called_once()
    assert '"submitted": 1' in capsys.readouterr().out
