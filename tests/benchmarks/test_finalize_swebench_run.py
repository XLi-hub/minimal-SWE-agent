"""Tests for the reproducible SWE-bench run finalizer."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from scripts.finalize_swebench_run import finalize_run


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _make_trajectory(run_dir: Path, instance_id: str, cost: float, calls: int) -> None:
    instance_dir = run_dir / instance_id
    _write_json(
        instance_dir / f"{instance_id}.traj.json",
        {
            "instance_id": instance_id,
            "event_log": {
                "path": f"{instance_id}.events.jsonl",
                "event_count": 1,
            },
            "info": {
                "exit_status": "submitted",
                "model_stats": {"instance_cost": cost, "api_calls": calls},
            },
        },
    )
    (instance_dir / f"{instance_id}.events.jsonl").write_text(
        '{"sequence": 0}\n', encoding="utf-8"
    )


def _make_report(
    path: Path,
    instance_id: str,
    outcome: str,
    *,
    ambiguous: bool = False,
    reason: str | None = None,
    mtime_ns: int,
) -> None:
    key = {
        "resolved": "resolved_ids",
        "unresolved": "unresolved_ids",
        "error": "error_ids",
        "infra_failure": "infra_failure_ids",
    }[outcome]
    report = {
        "resolved_ids": [],
        "unresolved_ids": [],
        "error_ids": [],
        "infra_failure_ids": [],
        "ambiguous_failure_ids": [instance_id] if ambiguous else [],
        "failure_reasons": {instance_id: reason} if reason else {},
    }
    report[key] = [instance_id]
    _write_json(path, report)
    os.utime(path, ns=(mtime_ns, mtime_ns))


def _make_run(tmp_path: Path) -> tuple[Path, Path]:
    run_dir = tmp_path / "run"
    instance_ids = ["repo__project-1", "repo__project-2"]
    predictions = {
        instance_id: {
            "instance_id": instance_id,
            "model_name_or_path": "model",
            "model_patch": f"patch-{instance_id}",
        }
        for instance_id in instance_ids
    }
    statuses = {
        instance_id: {
            "instance_id": instance_id,
            "exit_status": "submitted",
            "submission": predictions[instance_id]["model_patch"],
        }
        for instance_id in instance_ids
    }
    _write_json(run_dir / "preds.json", predictions)
    _write_json(run_dir / "statuses.json", statuses)
    (run_dir / "preds.jsonl").write_text(
        "".join(json.dumps(predictions[instance_id]) + "\n" for instance_id in instance_ids),
        encoding="utf-8",
    )
    _make_trajectory(run_dir, instance_ids[0], 0.1, 10)
    _make_trajectory(run_dir, instance_ids[1], 0.2, 20)

    reports = run_dir / "reports"
    _make_report(
        reports / "a-initial-error.json",
        instance_ids[0],
        "error",
        ambiguous=True,
        reason="tests_timed_out",
        mtime_ns=1_000_000_000_000,
    )
    selected = reports / "b-selected-resolved.json"
    _make_report(
        selected,
        instance_ids[0],
        "resolved",
        mtime_ns=2_000_000_000_000,
    )
    _make_report(
        reports / "c-newer-error.json",
        instance_ids[0],
        "error",
        mtime_ns=3_000_000_000_000,
    )
    _make_report(
        reports / "d-unresolved.json",
        instance_ids[1],
        "unresolved",
        reason="test_failure",
        mtime_ns=4_000_000_000_000,
    )

    retry = run_dir / "retry-history" / instance_ids[0] / "attempt-1"
    _write_json(
        retry / f"{instance_ids[0]}.traj.json",
        {
            "info": {
                "exit_status": "max_time",
                "model_stats": {"instance_cost": 0.05, "api_calls": 5},
            }
        },
    )
    return run_dir, selected


def test_finalize_run_records_initial_override_retries_and_checksums(tmp_path):
    run_dir, selected = _make_run(tmp_path)
    annotations = run_dir / "annotations.json"
    _write_json(annotations, {"note": "audited"})

    summary = finalize_run(
        run_dir,
        expected_count=2,
        report_overrides={"repo__project-1": selected},
        annotations_path=annotations,
    )

    assert summary["initial_evaluation"] == {
        "resolved": 0,
        "unresolved": 1,
        "error": 1,
        "infra_failure": 0,
        "incomplete": 0,
        "ambiguous_flags": 1,
        "resolve_rate": 0.0,
        "wilson_95_interval": [0.0, pytest.approx(0.6576280471103807)],
    }
    assert summary["final_evaluation"]["resolved"] == 1
    assert summary["final_evaluation"]["unresolved"] == 1
    assert summary["final_evaluation"]["resolve_rate"] == 0.5
    assert summary["billing"]["current_trajectories_cost_usd"] == pytest.approx(0.3)
    assert summary["billing"]["retry_history_cost_usd"] == pytest.approx(0.05)
    assert summary["billing"]["total_cost_usd"] == pytest.approx(0.35)
    assert summary["billing"]["total_api_calls"] == 35

    manifest = json.loads((run_dir / "final" / "manifest.json").read_text())
    first = manifest["instances"][0]
    assert first["initial_evaluation"]["outcome"] == "error"
    assert first["initial_evaluation"]["ambiguous"] is True
    assert first["final_evaluation"]["outcome"] == "resolved"
    assert first["final_selection"] == "explicit_override"
    assert len(first["evaluation_attempts"]) == 3
    assert first["retry_history"][0]["exit_status"] == "max_time"
    assert manifest["annotations"] == {"note": "audited"}

    failures = json.loads((run_dir / "final" / "failures.json").read_text())
    assert failures["count"] == 1
    assert failures["instances"][0]["instance_id"] == "repo__project-2"

    checksum_lines = (run_dir / "final" / "checksums.sha256").read_text().splitlines()
    assert any(line.endswith("  ../preds.json") for line in checksum_lines)
    assert any(line.endswith("  manifest.json") for line in checksum_lines)
    for line in checksum_lines:
        expected_hash, relative_path = line.split("  ", 1)
        path = (run_dir / "final" / relative_path).resolve()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_hash


def test_finalize_run_rejects_missing_event_log(tmp_path):
    run_dir, _ = _make_run(tmp_path)
    missing = run_dir / "repo__project-2" / "repo__project-2.events.jsonl"
    missing.unlink()

    with pytest.raises(ValueError, match="missing or empty event log"):
        finalize_run(run_dir, expected_count=2)


def test_finalize_run_accepts_grouped_instance_directories(tmp_path):
    run_dir, _ = _make_run(tmp_path)
    grouped = run_dir / "instances"
    grouped.mkdir()
    for instance_id in ("repo__project-1", "repo__project-2"):
        (run_dir / instance_id).rename(grouped / instance_id)

    finalize_run(run_dir, expected_count=2)

    manifest = json.loads((run_dir / "final" / "manifest.json").read_text())
    assert manifest["instances"][0]["trajectory"].startswith("instances/")
