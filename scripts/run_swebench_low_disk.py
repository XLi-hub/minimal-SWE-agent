#!/usr/bin/env python3
"""Run SWE-bench one instance at a time with conservative Docker cleanup.

This wrapper deliberately shells out to the package's existing generation and
evaluation entry points. The caller should launch it inside the project's
benchmark environment; this module uses :data:`sys.executable` for child
commands and never starts a second environment manager.

The normal benchmark runner can use several workers and keeps images around
until Docker decides to reclaim their layers.  That is useful for throughput,
but is a poor fit for a small Docker/containerd partition.  This script keeps
one generation and one official evaluation in flight, writes a small manifest
after each instance, and removes only that instance's SWE-bench image in a
``finally`` block.  ``docker image prune`` is an optional additional hint to
containerd and is run only when a preflight dangling-image check is clean.
It never invokes Docker's broad system-level cleanup.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import shlex
import subprocess
import sys
import traceback
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any


# ``python scripts/run_swebench_low_disk.py`` works after ``pip install -e .``
# as documented.  The fallback keeps the script convenient from a checkout
# when only ``PYTHONPATH=src`` has not been configured.
try:  # pragma: no cover - the normal import path is exercised by the tests
    from mini_agent.benchmarks._swebench.dataset import get_swebench_docker_image_name
    from mini_agent.persistence import atomic_write_text
except ModuleNotFoundError:  # pragma: no cover - direct checkout convenience
    _repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(_repo_root / "src"))
    from mini_agent.benchmarks._swebench.dataset import get_swebench_docker_image_name
    from mini_agent.persistence import atomic_write_text


MANIFEST_FILENAME = "low_disk_status.json"
DEFAULT_DOCKER_EXECUTABLE = os.getenv("MSWEA_DOCKER_EXECUTABLE", "docker")

Command = Sequence[str]
CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class InstanceRef:
    """An explicit instance selection and its optional dataset image name."""

    instance_id: str
    image: str | None = None

    @property
    def resolved_image(self) -> str:
        return get_swebench_docker_image_name(
            {"instance_id": self.instance_id, "image": self.image}
        )


@dataclass(frozen=True)
class CleanupResult:
    """Results of exact image deletion and the optional conservative GC hint."""

    image: str
    remove_returncode: int | None
    dangling_check_returncode: int | None
    dangling_images: tuple[str, ...]
    prune_returncode: int | None
    skipped_prune_reason: str | None = None
    exception: str | None = None

    @property
    def ok(self) -> bool:
        return self.remove_returncode in (None, 0) and self.exception is None


def _nonnegative_float(value: str) -> float:
    """argparse type for finite, non-negative token prices and caps."""

    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected a number, got {value!r}") from exc
    if not math.isfinite(parsed) or parsed < 0:
        raise argparse.ArgumentTypeError("value must be a finite non-negative number")
    return parsed


def _normalise_instance_id(value: Any) -> str:
    if value is None:
        raise ValueError("instance id must not be null")
    instance_id = str(value).strip()
    if not instance_id:
        raise ValueError("instance id must not be empty")
    if any(char.isspace() for char in instance_id):
        raise ValueError(f"instance id must not contain whitespace: {instance_id!r}")
    # The core runner uses the id as a directory name.  Rejecting separators
    # here makes a file of explicit ids safe before a child process is started.
    if Path(instance_id).name != instance_id or instance_id in {".", ".."}:
        raise ValueError(f"instance id cannot contain path separators: {instance_id!r}")
    return instance_id


def _instance_ref(value: Any) -> InstanceRef:
    if isinstance(value, InstanceRef):
        return value
    if isinstance(value, Mapping):
        if value.get("instance_id") is None:
            raise ValueError("instance records must contain instance_id")
        image_value = (
            value.get("image")
            or value.get("image_name")
            or value.get("docker_image")
        )
        image = str(image_value).strip() if image_value else None
        return InstanceRef(_normalise_instance_id(value["instance_id"]), image)
    return InstanceRef(_normalise_instance_id(value))


def _records_from_json(value: Any) -> list[Any]:
    """Extract records from common JSON/JSONL instance-file shapes."""

    if isinstance(value, list):
        return value
    if isinstance(value, Mapping):
        if "instances" in value:
            records = value["instances"]
            if not isinstance(records, list):
                raise ValueError("the instances field must be a JSON list")
            return records
        if "instance_id" in value:
            return [value]
        # A keyed predictions-like JSON object is accepted as a convenience:
        # {"owner__repo-1": {"image": "..."}, ...}.
        records: list[Any] = []
        for instance_id, record in value.items():
            if isinstance(record, Mapping):
                records.append({"instance_id": instance_id, **record})
            else:
                records.append(instance_id)
        return records
    raise ValueError("instance JSON must be a list or object")


def load_instance_file(path: str | Path) -> list[InstanceRef]:
    """Read explicit ids from plain text, JSON, or JSONL.

    Plain text uses one id per line; blank lines and ``#`` comments are
    ignored.  JSON may be a list of ids/records, one record, an ``instances``
    list, or a mapping keyed by id.  JSONL accepts one id or record per line.
    A record may carry ``image``, ``image_name``, or ``docker_image`` so a
    custom dataset image can still be removed exactly.
    """

    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise OSError(f"cannot read instance file {source}: {exc}") from exc

    suffix = source.suffix.lower()
    records: list[Any]
    if suffix == ".jsonl":
        records = []
        for line_number, line in enumerate(text.splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at {source}:{line_number}: {exc}") from exc
    elif suffix == ".json":
        try:
            records = _records_from_json(json.loads(text))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON in {source}: {exc}") from exc
    else:
        records = [
            line.split("#", 1)[0].strip()
            for line in text.splitlines()
            if line.split("#", 1)[0].strip()
        ]

    result: list[InstanceRef] = []
    for record in records:
        result.append(_instance_ref(record))
    return result


# Names that are convenient for callers/tests and make the file format helper
# discoverable without requiring knowledge of this script's implementation.
read_instance_file = load_instance_file
parse_instance_file = load_instance_file


def select_instances(
    instance_ids: Iterable[str] = (),
    instance_files: Iterable[str | Path] = (),
) -> list[InstanceRef]:
    """Combine explicit CLI ids and files, de-duplicating in first-seen order."""

    refs: list[InstanceRef] = [_instance_ref(value) for value in instance_ids]
    for path in instance_files:
        refs.extend(load_instance_file(path))
    if not refs:
        raise ValueError(
            "provide at least one explicit --instance/--instance-id or --instances-file"
        )

    deduplicated: list[InstanceRef] = []
    positions: dict[str, int] = {}
    for ref in refs:
        position = positions.get(ref.instance_id)
        if position is not None:
            # Preserve first-seen ordering while allowing a later JSON record
            # to supply the exact image for an id first listed as plain text.
            if deduplicated[position].image is None and ref.image is not None:
                deduplicated[position] = ref
            continue
        positions[ref.instance_id] = len(deduplicated)
        deduplicated.append(ref)
    return deduplicated


def estimate_cost_ceiling(instance_count: int, cost_limit: float | None) -> float | None:
    """Return the worst-case generation USD cap for a serial selection.

    ``agent.cost_limit`` is applied independently by each generation process;
    official evaluation does not issue model-provider calls.  A disabled cap
    (``None`` or ``0``) therefore has no finite ceiling.
    """

    if instance_count < 0:
        raise ValueError("instance_count must be non-negative")
    if cost_limit is None or cost_limit == 0:
        return None
    if cost_limit < 0 or not math.isfinite(cost_limit):
        raise ValueError("cost_limit must be finite and non-negative")
    return round(instance_count * cost_limit, 8)


def _format_number(value: float) -> str:
    return format(value, ".15g")


def _config_overrides(args: argparse.Namespace) -> list[str]:
    """Translate provider and cost flags to existing benchmark CLI overrides."""

    specs = list(getattr(args, "config", ()) or ())
    if args.provider is not None:
        specs.append(f"model.base_url={args.provider}")
    if args.api_key_env is not None:
        specs.append(f"model.api_key_env={args.api_key_env}")
    if args.input_price is not None:
        specs.append(f"cost.price_input_per_1m={_format_number(args.input_price)}")
    if args.cache_input_price is not None:
        specs.append(
            "cost.price_input_cache_hit_per_1m="
            f"{_format_number(args.cache_input_price)}"
        )
    if args.output_price is not None:
        specs.append(f"cost.price_output_per_1m={_format_number(args.output_price)}")
    if args.cost_limit is not None:
        specs.append(f"agent.cost_limit={_format_number(args.cost_limit)}")
    return specs


def _generation_command(
    ref: InstanceRef,
    *,
    output_dir: Path,
    subset: str,
    split: str,
    model: str | None,
    config_specs: Sequence[str],
    retry_failed: bool,
    redo_existing: bool,
    python_executable: str,
) -> list[str]:
    command = [
        python_executable,
        "-m",
        "mini_agent.benchmarks.cli",
        "--subset",
        subset,
        "--split",
        split,
        "--instance",
        ref.instance_id,
        "--workers",
        "1",
        "--output",
        str(output_dir),
        "--jsonl",
        str(output_dir / "preds.jsonl"),
    ]
    if model is not None:
        command.extend(["--model", model])
    for spec in config_specs:
        command.extend(["--config", spec])
    if retry_failed:
        command.append("--retry-failed")
    if redo_existing:
        command.append("--redo-existing")
    return command


def _safe_run_id(prefix: str, instance_id: str) -> str:
    value = "".join(
        char if char.isalnum() or char in ".-_" else "-"
        for char in f"{prefix}-{instance_id}"
    ).strip("-._")
    if not value or not value[0].isalnum():
        value = f"run-{value}"
    # Keep paths manageable while retaining the instance suffix for humans.
    return value[:180]


def _evaluation_command(
    ref: InstanceRef,
    *,
    output_dir: Path,
    subset: str,
    split: str,
    run_id_prefix: str,
    timeout: int,
    python_executable: str,
) -> list[str]:
    return [
        python_executable,
        "-m",
        "mini_agent.benchmarks.evaluation",
        str(output_dir / "preds.jsonl"),
        "--dataset",
        subset,
        "--split",
        split,
        "--workers",
        "1",
        "--run-id",
        _safe_run_id(run_id_prefix, ref.instance_id),
        "--timeout",
        str(timeout),
        "--report-dir",
        str(output_dir / "reports"),
        "--instance",
        ref.instance_id,
    ]


def _docker_command(executable: str, *parts: str) -> list[str]:
    return [executable, *parts]


def _invoke(
    command: Command,
    *,
    runner: CommandRunner | None = None,
) -> subprocess.CompletedProcess[str]:
    execute = runner or subprocess.run
    return execute(command, capture_output=True, text=True, check=False)


def _command_text(command: Command) -> str:
    return shlex.join(str(part) for part in command)


def _emit_process_output(
    label: str,
    completed: subprocess.CompletedProcess[str],
) -> None:
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    if stdout:
        print(f"[{label}] stdout:\n{stdout}", end="" if stdout.endswith("\n") else "\n")
    if stderr:
        print(
            f"[{label}] stderr:\n{stderr}",
            file=sys.stderr,
            end="" if stderr.endswith("\n") else "\n",
        )


def _warn(message: str) -> None:
    print(f"Warning: {message}", file=sys.stderr)


def cleanup_instance_image(
    image: str,
    *,
    docker_executable: str = DEFAULT_DOCKER_EXECUTABLE,
    image_prune: bool = True,
    dry_run: bool = False,
    runner: CommandRunner | None = None,
) -> CleanupResult:
    """Remove exactly *image*, then conditionally ask Docker for image GC.

    The dangling-image check is immediately before ``image prune``.  Any
    existing dangling image causes a warning and skips the broad operation;
    a failed check also skips it.  The exact ``image rm`` is still attempted
    regardless of the check and is safe to call from ``finally``.
    """

    image = str(image)
    remove_command = _docker_command(docker_executable, "image", "rm", image)
    if dry_run:
        print(f"[dry-run] {_command_text(remove_command)}")
        if image_prune:
            check_command = _docker_command(
                docker_executable,
                "image",
                "ls",
                "--filter",
                "dangling=true",
                "--quiet",
            )
            prune_command = _docker_command(docker_executable, "image", "prune", "--force")
            print(f"[dry-run] {_command_text(check_command)}")
            print(
                "[dry-run] "
                f"if dangling-image check is empty: {_command_text(prune_command)}"
            )
        return CleanupResult(
            image=image,
            remove_returncode=None,
            dangling_check_returncode=None,
            dangling_images=(),
            prune_returncode=None,
            skipped_prune_reason="dry-run" if image_prune else "disabled",
        )

    remove_returncode: int | None = None
    try:
        removed = _invoke(remove_command, runner=runner)
        remove_returncode = removed.returncode
        _emit_process_output(f"cleanup {image}", removed)
        if removed.returncode != 0:
            _warn(f"could not remove exact SWE-bench image {image!r} (return code {removed.returncode})")
    except Exception as exc:
        _warn(f"image cleanup command failed for {image!r}: {exc}")
        return CleanupResult(
            image=image,
            remove_returncode=remove_returncode,
            dangling_check_returncode=None,
            dangling_images=(),
            prune_returncode=None,
            skipped_prune_reason="image removal command failed",
            exception=repr(exc),
        )

    if not image_prune:
        return CleanupResult(
            image=image,
            remove_returncode=remove_returncode,
            dangling_check_returncode=None,
            dangling_images=(),
            prune_returncode=None,
            skipped_prune_reason="disabled",
        )

    check_command = _docker_command(
        docker_executable,
        "image",
        "ls",
        "--filter",
        "dangling=true",
        "--quiet",
    )
    try:
        checked = _invoke(check_command, runner=runner)
    except Exception as exc:
        _warn(f"dangling-image precheck failed; skipping image prune: {exc}")
        return CleanupResult(
            image=image,
            remove_returncode=remove_returncode,
            dangling_check_returncode=None,
            dangling_images=(),
            prune_returncode=None,
            skipped_prune_reason="dangling-image precheck failed",
            exception=repr(exc),
        )

    dangling = tuple(line.strip() for line in (checked.stdout or "").splitlines() if line.strip())
    if checked.returncode != 0:
        _warn(
            "dangling-image precheck returned "
            f"{checked.returncode}; skipping image prune"
        )
        return CleanupResult(
            image=image,
            remove_returncode=remove_returncode,
            dangling_check_returncode=checked.returncode,
            dangling_images=dangling,
            prune_returncode=None,
            skipped_prune_reason="dangling-image precheck failed",
        )
    if dangling:
        _warn(
            "dangling images already exist; skipping image prune to avoid removing "
            f"unrelated resources ({', '.join(dangling)})"
        )
        return CleanupResult(
            image=image,
            remove_returncode=remove_returncode,
            dangling_check_returncode=checked.returncode,
            dangling_images=dangling,
            prune_returncode=None,
            skipped_prune_reason="dangling images present",
        )

    prune_command = _docker_command(docker_executable, "image", "prune", "--force")
    try:
        pruned = _invoke(prune_command, runner=runner)
        _emit_process_output("image GC", pruned)
        if pruned.returncode != 0:
            _warn(f"image prune failed (return code {pruned.returncode})")
        return CleanupResult(
            image=image,
            remove_returncode=remove_returncode,
            dangling_check_returncode=checked.returncode,
            dangling_images=(),
            prune_returncode=pruned.returncode,
            skipped_prune_reason=None if pruned.returncode == 0 else "image prune failed",
        )
    except Exception as exc:
        _warn(f"image prune command failed: {exc}")
        return CleanupResult(
            image=image,
            remove_returncode=remove_returncode,
            dangling_check_returncode=checked.returncode,
            dangling_images=(),
            prune_returncode=None,
            skipped_prune_reason="image prune command failed",
            exception=repr(exc),
        )


def _load_manifest(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _warn(f"cannot read {path}; treating it as empty checkpoint: {exc}")
        return {}
    if not isinstance(value, Mapping):
        _warn(f"checkpoint {path} is not a JSON object; treating it as empty")
        return {}
    result: dict[str, dict[str, Any]] = {}
    for instance_id, record in value.items():
        if isinstance(record, Mapping):
            result[str(instance_id)] = dict(record)
    return result


def _save_manifest(path: Path, manifest: Mapping[str, Mapping[str, Any]]) -> None:
    atomic_write_text(
        path,
        json.dumps(manifest, indent=2, ensure_ascii=False, default=repr) + "\n",
    )


def _generation_status(output_dir: Path, instance_id: str) -> str | None:
    statuses_path = output_dir / "statuses.json"
    if not statuses_path.exists():
        return None
    try:
        statuses = json.loads(statuses_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    record = statuses.get(instance_id) if isinstance(statuses, Mapping) else None
    if not isinstance(record, Mapping):
        return None
    value = record.get("exit_status")
    return str(value) if value is not None else None


def _record_complete(record: Mapping[str, Any]) -> bool:
    return (
        record.get("generation_returncode") == 0
        and record.get("generation_status") in {None, "submitted"}
        and record.get("evaluation_returncode") == 0
        and record.get("cleanup_ok") is True
    )


def _serialise_cleanup(result: CleanupResult) -> dict[str, Any]:
    return {
        "image": result.image,
        "remove_returncode": result.remove_returncode,
        "dangling_check_returncode": result.dangling_check_returncode,
        "dangling_images": list(result.dangling_images),
        "prune_returncode": result.prune_returncode,
        "skipped_prune_reason": result.skipped_prune_reason,
        "exception": result.exception,
        "ok": result.ok,
    }


def run_instances(
    instances: Sequence[InstanceRef],
    *,
    output_dir: str | Path,
    subset: str = "verified",
    split: str = "test",
    model: str | None = None,
    provider: str | None = None,
    api_key_env: str | None = None,
    input_price: float | None = None,
    cache_input_price: float | None = None,
    output_price: float | None = None,
    cost_limit: float | None = None,
    config_specs: Sequence[str] = (),
    retry_failed: bool = False,
    redo_existing: bool = False,
    image_prune: bool = True,
    docker_executable: str = DEFAULT_DOCKER_EXECUTABLE,
    eval_timeout: int = 1800,
    run_id_prefix: str = "lowdisk",
    dry_run: bool = False,
    python_executable: str | None = None,
    runner: CommandRunner | None = None,
) -> list[dict[str, Any]]:
    """Run selected instances serially and return one manifest record each."""

    if not instances:
        raise ValueError("at least one instance is required")
    instances = [_instance_ref(instance) for instance in instances]
    if eval_timeout < 1:
        raise ValueError("eval_timeout must be at least 1")
    output = Path(output_dir)
    if not dry_run:
        output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / MANIFEST_FILENAME
    manifest = _load_manifest(manifest_path) if not dry_run else {}
    executable = python_executable or sys.executable
    specs = list(config_specs)
    # Keep this function usable independently of argparse while retaining the
    # same provider/cost override behavior as the command-line entry point.
    if provider is not None:
        specs.append(f"model.base_url={provider}")
    if api_key_env is not None:
        specs.append(f"model.api_key_env={api_key_env}")
    if input_price is not None:
        specs.append(f"cost.price_input_per_1m={_format_number(input_price)}")
    if cache_input_price is not None:
        specs.append(
            "cost.price_input_cache_hit_per_1m="
            f"{_format_number(cache_input_price)}"
        )
    if output_price is not None:
        specs.append(f"cost.price_output_per_1m={_format_number(output_price)}")
    if cost_limit is not None:
        specs.append(f"agent.cost_limit={_format_number(cost_limit)}")

    results: list[dict[str, Any]] = []
    ceiling = estimate_cost_ceiling(len(instances), cost_limit)
    if not dry_run:
        print(
            f"Serial SWE-bench run: {len(instances)} instance(s); "
            f"per-instance cost cap="
            f"{'disabled' if not cost_limit else '$' + _format_number(cost_limit)}"
            f"; worst-case generation cap="
            f"{'unbounded' if ceiling is None else '$' + _format_number(ceiling)}"
        )
    else:
        print(
            f"[dry-run] {len(instances)} instance(s); "
            f"worst-case generation cap="
            f"{'unbounded' if ceiling is None else '$' + _format_number(ceiling)}"
        )

    for ref in instances:
        instance_id = ref.instance_id
        previous = manifest.get(instance_id, {})
        if not dry_run and not redo_existing and _record_complete(previous):
            print(f"[{instance_id}] checkpoint complete; skipping")
            results.append(dict(previous))
            continue

        generation = _generation_command(
            ref,
            output_dir=output,
            subset=subset,
            split=split,
            model=model,
            config_specs=specs,
            retry_failed=retry_failed,
            redo_existing=redo_existing,
            python_executable=executable,
        )
        evaluation = _evaluation_command(
            ref,
            output_dir=output,
            subset=subset,
            split=split,
            run_id_prefix=run_id_prefix,
            timeout=eval_timeout,
            python_executable=executable,
        )
        if dry_run:
            print(f"[{instance_id}] generation: {_command_text(generation)}")
            print(f"[{instance_id}] evaluation: {_command_text(evaluation)}")
            cleanup = cleanup_instance_image(
                ref.resolved_image,
                docker_executable=docker_executable,
                image_prune=image_prune,
                dry_run=True,
                runner=runner,
            )
            results.append(
                {
                    "instance_id": instance_id,
                    "image": ref.resolved_image,
                    "generation_command": generation,
                    "evaluation_command": evaluation,
                    "cleanup": _serialise_cleanup(cleanup),
                    "dry_run": True,
                }
            )
            continue

        record: dict[str, Any] = {
            "instance_id": instance_id,
            "image": ref.resolved_image,
            "generation_command": generation,
            "evaluation_command": evaluation,
            "generation_returncode": None,
            "generation_status": None,
            "evaluation_returncode": None,
            "evaluation_skipped": None,
            "cleanup_ok": False,
        }
        try:
            print(f"[{instance_id}] generation (serial)")
            try:
                generated = _invoke(generation, runner=runner)
                record["generation_returncode"] = generated.returncode
                _emit_process_output(f"{instance_id} generation", generated)
                record["generation_status"] = _generation_status(output, instance_id)
            except Exception as exc:
                record["generation_exception"] = str(exc)
                record["generation_traceback"] = traceback.format_exc()
                _warn(f"{instance_id}: generation command failed: {exc}")

            # The existing CLI returns process code 0 even for a recorded
            # max_steps/cost_limit/error result.  Do not send an empty/failed
            # prediction to the harness; it remains retryable in this same
            # output directory.  A missing status is tolerated for custom
            # runners and tests that provide only a CompletedProcess.
            generation_ok = (
                record["generation_returncode"] == 0
                and record["generation_status"] in {None, "submitted"}
            )
            if generation_ok:
                print(f"[{instance_id}] official evaluation (one instance)")
                try:
                    evaluated = _invoke(evaluation, runner=runner)
                    record["evaluation_returncode"] = evaluated.returncode
                    _emit_process_output(f"{instance_id} evaluation", evaluated)
                except Exception as exc:
                    record["evaluation_exception"] = str(exc)
                    record["evaluation_traceback"] = traceback.format_exc()
                    _warn(f"{instance_id}: evaluation command failed: {exc}")
            else:
                record["evaluation_skipped"] = "generation did not submit a valid prediction"
                _warn(
                    f"{instance_id}: skipping official evaluation because generation "
                    "did not submit a valid prediction; use --retry-failed to retry"
                )
        finally:
            # Image deletion is intentionally the last operation for this
            # instance and runs on command errors, failed evaluation, and Ctrl-C.
            cleanup = cleanup_instance_image(
                ref.resolved_image,
                docker_executable=docker_executable,
                image_prune=image_prune,
                dry_run=False,
                runner=runner,
            )
            record["cleanup"] = _serialise_cleanup(cleanup)
            record["cleanup_ok"] = cleanup.ok
            manifest[instance_id] = record
            _save_manifest(manifest_path, manifest)

        results.append(record)

    return results


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="run_swebench_low_disk",
        description=(
            "Run explicit SWE-bench instances serially, evaluate one at a time, "
            "and reclaim each instance image safely."
        ),
    )
    parser.add_argument(
        "-i",
        "--instance",
        "--instance-id",
        dest="instance_ids",
        action="append",
        default=[],
        help="Exact instance id; repeat for multiple ids.",
    )
    parser.add_argument(
        "--instances-file",
        "--instance-file",
        "--ids-file",
        dest="instance_files",
        action="append",
        default=[],
        metavar="PATH",
        help="Plain ids (one per line), JSON, or JSONL instance file; repeatable.",
    )
    parser.add_argument("--subset", default="verified", help="Dataset alias or Hugging Face path")
    parser.add_argument("--split", default="test", help="Dataset split")
    parser.add_argument(
        "-o",
        "--output",
        "--output-dir",
        required=True,
        help="Shared checkpoint/output directory",
    )
    parser.add_argument("-m", "--model", default=None, help="Model id (for example deepseek-flash)")
    parser.add_argument(
        "--provider",
        "--provider-url",
        "--provider-base-url",
        "--base-url",
        dest="provider",
        default=None,
        help="OpenAI-compatible provider base URL",
    )
    parser.add_argument("--api-key-env", default=None, help="Environment variable containing provider key")
    parser.add_argument(
        "-c",
        "--config",
        action="append",
        default=[],
        metavar="SPEC",
        help="Forward a YAML path or key=value override to generation; repeatable.",
    )
    parser.add_argument(
        "--input-price",
        "--price-input",
        "--input-price-per-1m",
        "--price-input-per-1m",
        dest="input_price",
        type=_nonnegative_float,
        default=None,
        help="USD per 1M uncached input tokens",
    )
    parser.add_argument(
        "--cache-input-price",
        "--price-cache-input",
        "--cache-input-price-per-1m",
        "--cache-hit-price",
        "--cache-hit-price-per-1m",
        "--price-input-cache-hit-per-1m",
        dest="cache_input_price",
        type=_nonnegative_float,
        default=None,
        help="USD per 1M cache-hit input tokens",
    )
    parser.add_argument(
        "--output-price",
        "--price-output",
        "--output-price-per-1m",
        "--price-output-per-1m",
        dest="output_price",
        type=_nonnegative_float,
        default=None,
        help="USD per 1M output tokens",
    )
    parser.add_argument(
        "--cost-limit",
        "--cost-limit-usd",
        dest="cost_limit",
        type=_nonnegative_float,
        default=None,
        help="Per-instance generation USD cap; zero disables the cap",
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Retry recorded generation/evaluation failures in the same output directory",
    )
    parser.add_argument(
        "--redo-existing",
        action="store_true",
        help="Regenerate even instances with an existing prediction",
    )
    parser.add_argument(
        "--docker-executable",
        default=DEFAULT_DOCKER_EXECUTABLE,
        help="Docker-compatible executable used for image cleanup",
    )
    parser.add_argument(
        "--image-prune",
        "--gc",
        dest="image_prune",
        action="store_true",
        default=True,
        help="Enable the guarded image-prune GC hint (the default)",
    )
    parser.add_argument(
        "--no-image-prune",
        "--no-gc",
        dest="image_prune",
        action="store_false",
        default=True,
        help="Only remove the exact image; skip the guarded image-prune GC hint",
    )
    parser.add_argument(
        "--eval-timeout",
        type=int,
        default=1800,
        help="Official harness timeout in seconds",
    )
    parser.add_argument(
        "--run-id-prefix",
        default="lowdisk",
        help="Prefix for per-instance official harness report run ids",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print all per-instance commands without executing them")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        refs = select_instances(args.instance_ids, args.instance_files)
        results = run_instances(
            refs,
            output_dir=args.output,
            subset=args.subset,
            split=args.split,
            model=args.model,
            provider=args.provider,
            api_key_env=args.api_key_env,
            input_price=args.input_price,
            cache_input_price=args.cache_input_price,
            output_price=args.output_price,
            cost_limit=args.cost_limit,
            config_specs=args.config,
            retry_failed=args.retry_failed,
            redo_existing=args.redo_existing,
            image_prune=args.image_prune,
            docker_executable=args.docker_executable,
            eval_timeout=args.eval_timeout,
            run_id_prefix=args.run_id_prefix,
            dry_run=args.dry_run,
        )
    except KeyboardInterrupt:
        _warn("interrupted; the active instance cleanup was attempted")
        return 130
    except (OSError, ValueError, argparse.ArgumentError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    failures = [
        result
        for result in results
        if not result.get("dry_run")
        and (
            result.get("generation_returncode") != 0
            or result.get("generation_status") not in {None, "submitted"}
            or result.get("evaluation_returncode") != 0
            or result.get("cleanup_ok") is not True
        )
    ]
    if not args.dry_run:
        print(json.dumps({"processed": len(results), "failed": len(failures)}, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CleanupResult",
    "InstanceRef",
    "MANIFEST_FILENAME",
    "cleanup_instance_image",
    "estimate_cost_ceiling",
    "load_instance_file",
    "main",
    "parse_instance_file",
    "read_instance_file",
    "run_instances",
    "select_instances",
]
