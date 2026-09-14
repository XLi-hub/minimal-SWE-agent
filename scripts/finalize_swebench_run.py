#!/usr/bin/env python3
"""Validate a completed SWE-bench run and build a reproducible final index."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping


try:  # pragma: no cover - normal path after ``pip install -e .``
    from mini_agent.persistence import atomic_write_text
except ModuleNotFoundError:  # pragma: no cover - direct checkout convenience
    _repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(_repo_root / "src"))
    from mini_agent.persistence import atomic_write_text


OUTCOME_KEYS = (
    ("resolved", "resolved_ids"),
    ("error", "error_ids"),
    ("infra_failure", "infra_failure_ids"),
    ("unresolved", "unresolved_ids"),
    ("incomplete", "incomplete_ids"),
)
PRIMARY_FILES = ("preds.json", "preds.jsonl", "statuses.json")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read valid JSON from {path}: {exc}") from exc


def _require_mapping(value: Any, path: Path) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _read_predictions_jsonl(path: Path) -> dict[str, Mapping[str, Any]]:
    records: dict[str, Mapping[str, Any]] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON at {path}:{line_number}: {exc}") from exc
        if not isinstance(record, Mapping) or not record.get("instance_id"):
            raise ValueError(f"{path}:{line_number} lacks instance_id")
        instance_id = str(record["instance_id"])
        if instance_id in records:
            raise ValueError(f"duplicate prediction for {instance_id} in {path}")
        records[instance_id] = record
    return records


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _nonempty(path: Path, label: str) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f"missing or empty {label}: {path}")


def _instance_directory(run_dir: Path, instance_id: str) -> Path:
    """Resolve grouped and legacy per-instance layouts without ambiguity."""

    grouped = run_dir / "instances" / instance_id
    legacy = run_dir / instance_id
    if grouped.exists() and legacy.exists():
        raise ValueError(
            f"instance directory exists in grouped and legacy layouts: {instance_id}"
        )
    return grouped if grouped.exists() else legacy


def _report_outcome(report: Mapping[str, Any], instance_id: str) -> str | None:
    outcomes = [
        outcome
        for outcome, key in OUTCOME_KEYS
        if instance_id in report.get(key, [])
    ]
    if len(outcomes) > 1:
        raise ValueError(
            f"report classifies {instance_id} into multiple outcomes: {outcomes}"
        )
    return outcomes[0] if outcomes else None


def _report_attempt(
    path: Path,
    report: Mapping[str, Any],
    instance_id: str,
    root: Path,
) -> dict[str, Any] | None:
    outcome = _report_outcome(report, instance_id)
    if outcome is None:
        return None
    reasons = report.get("failure_reasons", {})
    reason = reasons.get(instance_id) if isinstance(reasons, Mapping) else None
    return {
        "report": _relative(path, root),
        "outcome": outcome,
        "ambiguous": instance_id in report.get("ambiguous_failure_ids", []),
        "reason": reason,
    }


def _parse_overrides(values: Iterable[str], root: Path) -> dict[str, Path]:
    overrides: dict[str, Path] = {}
    for value in values:
        instance_id, separator, raw_path = value.partition("=")
        if not separator or not instance_id.strip() or not raw_path.strip():
            raise ValueError("--report-override must use INSTANCE_ID=PATH")
        instance_id = instance_id.strip()
        path = Path(raw_path.strip()).expanduser()
        if not path.is_absolute():
            path = root / path
        path = path.resolve()
        if instance_id in overrides:
            raise ValueError(f"duplicate report override for {instance_id}")
        overrides[instance_id] = path
    return overrides


def _trajectory_stats(path: Path) -> tuple[float, int, str | None]:
    data = _require_mapping(_read_json(path), path)
    info = data.get("info", {})
    if not isinstance(info, Mapping):
        raise ValueError(f"trajectory info must be an object: {path}")
    stats = info.get("model_stats", {})
    if not isinstance(stats, Mapping):
        raise ValueError(f"trajectory model_stats must be an object: {path}")
    return (
        float(stats.get("instance_cost", 0.0) or 0.0),
        int(stats.get("api_calls", 0) or 0),
        str(info["exit_status"]) if info.get("exit_status") is not None else None,
    )


def _validate_current_trajectory(
    path: Path,
    events: Path,
    instance_id: str,
) -> tuple[float, int, str | None]:
    data = _require_mapping(_read_json(path), path)
    if data.get("instance_id") != instance_id:
        raise ValueError(f"trajectory instance_id mismatch: {path}")
    event_log = data.get("event_log")
    if not isinstance(event_log, Mapping):
        raise ValueError(f"trajectory lacks event_log metadata: {path}")
    if event_log.get("path") != events.name:
        raise ValueError(f"trajectory event_log path mismatch: {path}")
    expected_events = event_log.get("event_count")
    if not isinstance(expected_events, int) or expected_events < 1:
        raise ValueError(f"trajectory has invalid event_count: {path}")
    actual_events = sum(
        1 for line in events.read_text(encoding="utf-8").splitlines() if line.strip()
    )
    if actual_events != expected_events:
        raise ValueError(
            f"event count mismatch for {instance_id}: "
            f"trajectory={expected_events}, events={actual_events}"
        )
    return _trajectory_stats(path)


def _wilson_interval(successes: int, total: int, z: float = 1.96) -> list[float]:
    if total <= 0:
        return [0.0, 0.0]
    proportion = successes / total
    denominator = 1 + z * z / total
    centre = (proportion + z * z / (2 * total)) / denominator
    half_width = (
        z
        * math.sqrt(
            proportion * (1 - proportion) / total + z * z / (4 * total * total)
        )
        / denominator
    )
    return [centre - half_width, centre + half_width]


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_checksums(final_dir: Path, files: Iterable[Path]) -> None:
    unique = sorted({path.resolve() for path in files if path.is_file()}, key=str)
    lines = [
        f"{_sha256(path)}  {os.path.relpath(path, final_dir)}"
        for path in unique
        if path.resolve() != (final_dir / "checksums.sha256").resolve()
    ]
    atomic_write_text(final_dir / "checksums.sha256", "\n".join(lines) + "\n")


def finalize_run(
    run_dir: Path,
    *,
    expected_count: int,
    report_overrides: Mapping[str, Path] | None = None,
    annotations_path: Path | None = None,
) -> dict[str, Any]:
    """Validate *run_dir* and atomically create its ``final`` summary layer."""

    run_dir = run_dir.resolve()
    if not run_dir.is_dir():
        raise ValueError(f"run directory does not exist: {run_dir}")
    for filename in PRIMARY_FILES:
        _nonempty(run_dir / filename, filename)

    predictions = _require_mapping(_read_json(run_dir / "preds.json"), run_dir / "preds.json")
    statuses = _require_mapping(_read_json(run_dir / "statuses.json"), run_dir / "statuses.json")
    jsonl_predictions = _read_predictions_jsonl(run_dir / "preds.jsonl")
    instance_ids = sorted(str(key) for key in predictions)
    expected_ids = set(instance_ids)
    if len(instance_ids) != expected_count:
        raise ValueError(
            f"expected {expected_count} predictions, found {len(instance_ids)}"
        )
    if set(str(key) for key in statuses) != expected_ids:
        raise ValueError("statuses.json instance ids do not match preds.json")
    if set(jsonl_predictions) != expected_ids:
        raise ValueError("preds.jsonl instance ids do not match preds.json")

    for instance_id in instance_ids:
        prediction = predictions[instance_id]
        status = statuses[instance_id]
        if not isinstance(prediction, Mapping) or prediction.get("instance_id") != instance_id:
            raise ValueError(f"invalid keyed prediction for {instance_id}")
        if dict(prediction) != dict(jsonl_predictions[instance_id]):
            raise ValueError(f"preds.json and preds.jsonl disagree for {instance_id}")
        if not isinstance(status, Mapping) or status.get("instance_id") != instance_id:
            raise ValueError(f"invalid keyed status for {instance_id}")
        if status.get("exit_status") != "submitted":
            raise ValueError(f"{instance_id} is not submitted: {status.get('exit_status')!r}")
        if status.get("submission") != prediction.get("model_patch"):
            raise ValueError(f"status and prediction patches disagree for {instance_id}")

    overrides = dict(report_overrides or {})
    unknown_overrides = sorted(set(overrides) - expected_ids)
    if unknown_overrides:
        raise ValueError(f"report overrides contain unknown ids: {unknown_overrides}")

    report_data: dict[Path, Mapping[str, Any]] = {}
    for path in sorted((run_dir / "reports").glob("*.json")):
        _nonempty(path, "evaluation report")
        report_data[path.resolve()] = _require_mapping(_read_json(path), path)
    for path in overrides.values():
        _nonempty(path, "override report")
        report_data.setdefault(path.resolve(), _require_mapping(_read_json(path), path))

    annotations: Any = None
    if annotations_path is not None:
        annotations_path = annotations_path.resolve()
        _nonempty(annotations_path, "annotations")
        annotations = _read_json(annotations_path)

    current_cost = 0.0
    current_calls = 0
    retry_cost = 0.0
    retry_calls = 0
    checksum_files: set[Path] = {
        path for path in run_dir.iterdir() if path.is_file()
    }
    if annotations_path is not None:
        checksum_files.add(annotations_path)

    retry_by_instance: dict[str, list[dict[str, Any]]] = {}
    retry_root = run_dir / "retry-history"
    if retry_root.is_dir():
        checksum_files.update(path for path in retry_root.rglob("*") if path.is_file())
        for path in sorted(retry_root.rglob("*.traj.json")):
            _nonempty(path, "retry trajectory")
            cost, calls, exit_status = _trajectory_stats(path)
            instance_id = path.name.removesuffix(".traj.json")
            retry_cost += cost
            retry_calls += calls
            checksum_files.add(path)
            retry_by_instance.setdefault(instance_id, []).append(
                {
                    "trajectory": _relative(path, run_dir),
                    "exit_status": exit_status,
                    "cost_usd": cost,
                    "api_calls": calls,
                }
            )

    manifest_instances: list[dict[str, Any]] = []
    initial_counts: Counter[str] = Counter()
    final_counts: Counter[str] = Counter()
    initial_ambiguous = 0
    final_ambiguous = 0

    for instance_id in instance_ids:
        instance_dir = _instance_directory(run_dir, instance_id)
        trajectory = instance_dir / f"{instance_id}.traj.json"
        events = instance_dir / f"{instance_id}.events.jsonl"
        _nonempty(trajectory, "trajectory")
        _nonempty(events, "event log")
        cost, calls, exit_status = _validate_current_trajectory(
            trajectory, events, instance_id
        )
        if exit_status != "submitted":
            raise ValueError(f"trajectory for {instance_id} is not submitted: {exit_status!r}")
        current_cost += cost
        current_calls += calls
        checksum_files.update((trajectory, events))

        ordered_reports = sorted(
            report_data,
            key=lambda path: (path.stat().st_mtime_ns, str(path)),
        )
        attempts = []
        attempt_paths: list[Path] = []
        for path in ordered_reports:
            attempt = _report_attempt(path, report_data[path], instance_id, run_dir)
            if attempt is not None:
                attempts.append(attempt)
                attempt_paths.append(path)
                checksum_files.add(path)
        if not attempts:
            raise ValueError(f"no evaluation report classifies {instance_id}")

        initial = attempts[0]
        if instance_id in overrides:
            override_path = overrides[instance_id].resolve()
            try:
                final_index = attempt_paths.index(override_path)
            except ValueError as exc:
                raise ValueError(
                    f"override report does not classify {instance_id}: {override_path}"
                ) from exc
            final = attempts[final_index]
            selection = "explicit_override"
        else:
            final = attempts[-1]
            selection = "latest_report"

        initial_counts[initial["outcome"]] += 1
        final_counts[final["outcome"]] += 1
        initial_ambiguous += int(initial["ambiguous"])
        final_ambiguous += int(final["ambiguous"])
        manifest_instances.append(
            {
                "instance_id": instance_id,
                "prediction": "preds.json",
                "status": "statuses.json",
                "trajectory": _relative(trajectory, run_dir),
                "events": _relative(events, run_dir),
                "current_cost_usd": cost,
                "current_api_calls": calls,
                "retry_history": retry_by_instance.get(instance_id, []),
                "evaluation_attempts": attempts,
                "initial_evaluation": initial,
                "final_evaluation": final,
                "final_selection": selection,
            }
        )

    def outcome_summary(counts: Counter[str], ambiguous: int) -> dict[str, Any]:
        resolved = counts["resolved"]
        return {
            "resolved": resolved,
            "unresolved": counts["unresolved"],
            "error": counts["error"],
            "infra_failure": counts["infra_failure"],
            "incomplete": counts["incomplete"],
            "ambiguous_flags": ambiguous,
            "resolve_rate": resolved / expected_count,
            "wilson_95_interval": _wilson_interval(resolved, expected_count),
        }

    billing_total_cost = current_cost + retry_cost
    billing_total_calls = current_calls + retry_calls
    summary = {
        "schema_version": 1,
        "expected_instances": expected_count,
        "observed_instances": len(instance_ids),
        "submitted_instances": len(instance_ids),
        "initial_evaluation": outcome_summary(initial_counts, initial_ambiguous),
        "final_evaluation": outcome_summary(final_counts, final_ambiguous),
        "billing": {
            "current_trajectories_cost_usd": current_cost,
            "current_trajectories_api_calls": current_calls,
            "retry_history_cost_usd": retry_cost,
            "retry_history_api_calls": retry_calls,
            "total_cost_usd": billing_total_cost,
            "total_api_calls": billing_total_calls,
            "mean_cost_per_instance_usd": billing_total_cost / expected_count,
            "mean_api_calls_per_instance": billing_total_calls / expected_count,
            "cost_per_final_resolved_usd": (
                billing_total_cost / final_counts["resolved"]
                if final_counts["resolved"]
                else None
            ),
        },
    }
    manifest = {
        "schema_version": 1,
        "run_directory": ".",
        "expected_instances": expected_count,
        "primary_files": list(PRIMARY_FILES),
        "annotations_file": (
            _relative(annotations_path, run_dir) if annotations_path is not None else None
        ),
        "annotations": annotations,
        "instances": manifest_instances,
    }
    failures = {
        "schema_version": 1,
        "count": sum(
            1
            for item in manifest_instances
            if item["final_evaluation"]["outcome"] != "resolved"
        ),
        "instances": [
            {
                "instance_id": item["instance_id"],
                "initial_evaluation": item["initial_evaluation"],
                "final_evaluation": item["final_evaluation"],
                "evaluation_attempts": item["evaluation_attempts"],
            }
            for item in manifest_instances
            if item["final_evaluation"]["outcome"] != "resolved"
        ],
    }

    final_dir = run_dir / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    initial_result = summary["initial_evaluation"]
    final_result = summary["final_evaluation"]
    final_failures = expected_count - final_result["resolved"]
    readme = f"""# Final SWE-bench run index

This directory is the validated summary layer for the run in its parent directory.
Preserve the complete parent run directory; the manifest paths are relative to it.

## Final result

- Total: {expected_count}
- Successful: {final_result['resolved']} resolved ({final_result['resolve_rate']:.1%})
- Failed: {final_failures} not resolved
- Final failure statuses: {final_result['unresolved']} unresolved, \
{final_result['error']} error, {final_result['infra_failure']} infrastructure failure
- Total billed model cost including preserved retries: ${billing_total_cost:.8f}
- Total API calls including preserved retries: {billing_total_calls}

## Audit trail

- Earliest official reports: {initial_result['resolved']} resolved, \
{initial_result['unresolved']} unresolved, {initial_result['error']} error
- Final selected reports: {final_result['resolved']} resolved, \
{final_result['unresolved']} unresolved, {final_result['error']} error

`initial_evaluation` always uses the earliest classifying report for an instance.
`final_evaluation` uses an explicit override when supplied, otherwise the latest report.
An ambiguous classifier flag is recorded separately and does not replace the mutually
exclusive resolved/unresolved/error/infrastructure outcome.

## Files

- `summary.json`: aggregate initial/final scores, confidence intervals, and cost.
- `manifest.json`: per-instance trajectories, event logs, attempts, and retry history.
- `failures.json`: non-resolved instances with their evaluation evidence.
- `instances.txt`: exact sorted instance set.
- `annotations.json`: manual failure and retry audit when supplied.
- `raw-evaluation-logs.tar.zst`: archived raw harness logs when retained.
- `checksums.sha256`: hashes for the summary and every referenced raw artifact.

Verify the archive from this directory with `sha256sum -c checksums.sha256`.
"""

    output_files = {
        final_dir / "instances.txt": "\n".join(instance_ids) + "\n",
        final_dir / "manifest.json": _json_text(manifest),
        final_dir / "summary.json": _json_text(summary),
        final_dir / "failures.json": _json_text(failures),
        final_dir / "README.md": readme,
    }
    for path, text in output_files.items():
        atomic_write_text(path, text)
    checksum_files.update(output_files)
    checksum_files.update(
        path for path in final_dir.iterdir() if path.is_file() and path.name != "checksums.sha256"
    )
    _write_checksums(final_dir, checksum_files)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--expected-count", type=int, required=True)
    parser.add_argument(
        "--report-override",
        action="append",
        default=[],
        metavar="INSTANCE_ID=PATH",
        help="Select a final report explicitly while preserving the earliest initial result.",
    )
    parser.add_argument("--annotations", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.expected_count <= 0:
        parser.error("--expected-count must be positive")
    try:
        run_dir = args.run_dir.resolve()
        overrides = _parse_overrides(args.report_override, run_dir)
        annotations = args.annotations
        if annotations is not None and not annotations.is_absolute():
            annotations = run_dir / annotations
        summary = finalize_run(
            run_dir,
            expected_count=args.expected_count,
            report_overrides=overrides,
            annotations_path=annotations,
        )
    except ValueError as exc:
        parser.exit(2, f"error: {exc}\n")
    print(_json_text(summary), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
