"""Adapter for the official Docker-based SWE-bench evaluation harness."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from mini_agent.benchmarks.swebench import DATASET_MAPPING


ReportFingerprint = tuple[int, int, int, int]


@dataclass(frozen=True)
class HarnessResult:
    """Completed harness process plus any final reports it produced."""

    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    report_paths: tuple[Path, ...]
    reports: tuple[dict[str, Any], ...]

    @property
    def resolved(self) -> int:
        return sum(int(report.get("resolved_instances", 0)) for report in self.reports)

    @property
    def total(self) -> int:
        return sum(int(report.get("total_instances", 0)) for report in self.reports)

    @property
    def resolution_rate(self) -> float:
        return self.resolved / self.total if self.total else 0.0


def _validate_run_id(run_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", run_id):
        raise ValueError(
            "run_id must start with an alphanumeric character and contain only "
            "letters, numbers, dots, underscores, or hyphens"
        )
    return run_id


def build_harness_command(
    *,
    dataset: str,
    split: str,
    predictions_path: str | Path,
    max_workers: int,
    run_id: str,
    timeout: int = 1800,
    report_dir: str | Path = ".",
    instance_ids: Sequence[str] | None = None,
    python_executable: str = sys.executable,
) -> list[str]:
    """Build the official ``swebench.harness.run_evaluation`` command."""

    if max_workers < 1:
        raise ValueError("max_workers must be at least 1")
    if timeout < 1:
        raise ValueError("timeout must be at least 1")
    _validate_run_id(run_id)
    dataset_name = DATASET_MAPPING.get(dataset, dataset)
    command = [
        python_executable,
        "-m",
        "swebench.harness.run_evaluation",
        "--dataset_name",
        dataset_name,
        "--split",
        split,
        "--predictions_path",
        str(Path(predictions_path)),
        "--max_workers",
        str(max_workers),
        "--run_id",
        run_id,
        "--timeout",
        str(timeout),
        "--report_dir",
        str(Path(report_dir)),
    ]
    if instance_ids:
        command.extend(["--instance_ids", *map(str, instance_ids)])
    return command


def _report_fingerprint(path: Path) -> ReportFingerprint | None:
    """Capture enough file state to distinguish a fresh harness report."""
    try:
        stat = path.stat()
        return (
            stat.st_mtime_ns,
            stat.st_ctime_ns,
            stat.st_size,
            getattr(stat, "st_ino", 0),
        )
    except OSError:
        return None


def _snapshot_reports(report_dir: Path, run_id: str) -> dict[Path, ReportFingerprint]:
    """Record matching reports before invoking the harness."""
    snapshot: dict[Path, ReportFingerprint] = {}
    for path in report_dir.glob(f"*.{run_id}.json"):
        fingerprint = _report_fingerprint(path)
        if fingerprint is not None:
            snapshot[path] = fingerprint
    return snapshot


def _load_reports(
    report_dir: Path,
    run_id: str,
    *,
    before: dict[Path, ReportFingerprint] | None = None,
) -> tuple[tuple[Path, ...], tuple[dict[str, Any], ...]]:
    paths = tuple(sorted(report_dir.glob(f"*.{run_id}.json")))
    if before is not None:
        paths = tuple(
            path
            for path in paths
            if _report_fingerprint(path) != before.get(path)
        )
    reports: list[dict] = []
    valid_paths: list[Path] = []
    for path in paths:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            valid_paths.append(path)
            reports.append(value)
    return tuple(valid_paths), tuple(reports)


def evaluate_predictions(
    predictions_path: str | Path,
    *,
    dataset: str = "lite",
    split: str = "test",
    max_workers: int = 1,
    run_id: str = "minimal-swebench",
    timeout: int = 1800,
    report_dir: str | Path = ".",
    instance_ids: Sequence[str] | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> HarnessResult:
    """Run the official harness without importing it into the agent process."""

    predictions = Path(predictions_path)
    if not predictions.is_file():
        raise FileNotFoundError(predictions)
    if predictions.suffix not in {".json", ".jsonl"}:
        raise ValueError("predictions_path must end in .json or .jsonl")
    destination = Path(report_dir)
    destination.mkdir(parents=True, exist_ok=True)
    command = build_harness_command(
        dataset=dataset,
        split=split,
        predictions_path=predictions,
        max_workers=max_workers,
        run_id=run_id,
        timeout=timeout,
        report_dir=destination,
        instance_ids=instance_ids,
    )
    before = _snapshot_reports(destination, run_id)
    completed = runner(command, capture_output=True, text=True, check=False)
    report_paths, reports = _load_reports(destination, run_id, before=before)
    return HarnessResult(
        command=tuple(command),
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
        report_paths=report_paths,
        reports=reports,
    )


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="minimal-swebench-eval",
        description="Score predictions with the official SWE-bench harness.",
    )
    parser.add_argument("predictions_path")
    parser.add_argument("--dataset", default="lite")
    parser.add_argument("--split", default="test")
    parser.add_argument("-w", "--workers", type=int, default=1)
    parser.add_argument("--run-id", default="minimal-swebench")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--report-dir", default=".")
    parser.add_argument("-i", "--instance", action="append", default=[])
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    result = evaluate_predictions(
        args.predictions_path,
        dataset=args.dataset,
        split=args.split,
        max_workers=args.workers,
        run_id=args.run_id,
        timeout=args.timeout,
        report_dir=args.report_dir,
        instance_ids=args.instance or None,
    )
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="" if result.stderr.endswith("\n") else "\n")
    if result.reports:
        print(
            f"Resolved: {result.resolved}/{result.total} "
            f"({result.resolution_rate:.1%})"
        )
        for path in result.report_paths:
            print(f"Report: {path}")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
