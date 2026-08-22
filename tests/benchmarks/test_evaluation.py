import json
import subprocess

import pytest

from mini_agent.benchmarks.evaluation import (
    build_harness_command,
    evaluate_predictions,
)


def test_build_harness_command_uses_official_module_and_alias(tmp_path):
    command = build_harness_command(
        dataset="verified",
        split="test",
        predictions_path=tmp_path / "preds.jsonl",
        max_workers=4,
        run_id="smoke",
        timeout=900,
        report_dir=tmp_path,
        instance_ids=["repo__one-1"],
        python_executable="python-test",
    )
    assert command[:3] == ["python-test", "-m", "swebench.harness.run_evaluation"]
    assert "princeton-nlp/SWE-Bench_Verified" in command
    assert command[command.index("--max_workers") + 1] == "4"
    assert command[-2:] == ["--instance_ids", "repo__one-1"]


def test_evaluate_predictions_returns_parsed_report(tmp_path):
    predictions = tmp_path / "preds.jsonl"
    predictions.write_text('{"instance_id":"x"}\n')
    report_dir = tmp_path / "reports"

    def runner(command, **kwargs):
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / "model.smoke.json").write_text(
            json.dumps({"total_instances": 10, "resolved_instances": 4})
        )
        return subprocess.CompletedProcess(command, 0, stdout="done\n", stderr="")

    result = evaluate_predictions(
        predictions,
        dataset="lite",
        run_id="smoke",
        report_dir=report_dir,
        runner=runner,
    )

    assert result.returncode == 0
    assert result.resolved == 4
    assert result.total == 10
    assert result.resolution_rate == pytest.approx(0.4)
    assert result.report_paths == (report_dir / "model.smoke.json",)


def test_evaluate_predictions_validates_inputs(tmp_path):
    with pytest.raises(FileNotFoundError):
        evaluate_predictions(tmp_path / "missing.jsonl")
    predictions = tmp_path / "preds.txt"
    predictions.write_text("x")
    with pytest.raises(ValueError, match=".json"):
        evaluate_predictions(predictions)
    with pytest.raises(ValueError, match="run_id"):
        build_harness_command(
            dataset="lite",
            split="test",
            predictions_path="preds.jsonl",
            max_workers=1,
            run_id="../bad",
        )
    with pytest.raises(ValueError, match="run_id"):
        build_harness_command(
            dataset="lite",
            split="test",
            predictions_path="preds.jsonl",
            max_workers=1,
            run_id="bad*glob",
        )
