"""SWE-bench dataset and batch-running support.

This module contains the orchestration layer only.  Dataset loading, model
construction, environment construction, and agent construction are all
injectable, which makes the runner usable with the optional ``datasets``
package and straightforward to test without an API key or Docker daemon.

The default output layout is compatible with SWE-bench's harness::

    output/
      preds.json
      preds.jsonl
      statuses.json
      <instance_id>/<instance_id>.traj.json
      <instance_id>/<instance_id>.events.jsonl

``preds.json`` is a mapping keyed by instance id.  Every prediction value has
the standard SWE-bench fields ``model_name_or_path``, ``instance_id``, and
``model_patch``.  ``preds.jsonl`` contains the same values one per line.
"""

from __future__ import annotations

import concurrent.futures
import inspect
import json
import os
import random
import re
import tempfile
import threading
import traceback
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from mini_agent.agent import save_trajectory_data
# Environment construction itself remains lazy (no container is started by
# this import), while exposing the symbol keeps the compatibility helper easy
# to monkeypatch in tests.
from mini_agent.environments import get_environment


# Keep this mapping in the runner, rather than in the CLI, so Python callers
# can use exactly the same dataset aliases as command-line callers.
DATASET_MAPPING: dict[str, str] = {
    "full": "SWE-bench/SWE-bench",
    "verified": "SWE-bench/SWE-bench_Verified",
    "lite": "SWE-bench/SWE-bench_Lite",
    "multimodal": "SWE-bench/SWE-bench_Multimodal",
    "multilingual": "SWE-bench/SWE-bench_Multilingual",
    "smith": "SWE-bench/SWE-smith",
    "_test": "klieret/swe-bench-dummy-test-dataset",
    "rebench": "nebius/SWE-rebench",
}

# Trajectories are routinely browsed, shared, and reused for analysis.  Keep
# only public task/setup metadata; copying the dataset row wholesale would
# persist gold patches, hidden tests, and evaluation scripts beside the model
# trace even though none of them were sent to the model.
TRAJECTORY_INSTANCE_FIELDS: tuple[str, ...] = (
    "instance_id",
    "repo",
    "base_commit",
    "environment_setup_commit",
    "problem_statement",
    "version",
    "created_at",
    "difficulty",
    "eval_type",
    "image",
    "image_name",
    "docker_image",
)


def get_swebench_docker_image_name(instance: Mapping[str, Any]) -> str:
    """Return the Docker image associated with a SWE-bench instance.

    Dataset revisions have used ``image``, ``image_name``, and
    ``docker_image``.  When none is present, the conventional SWE-bench image
    name is derived from ``instance_id``.  Docker rejects double underscores
    in some image-name components, so SWE-bench uses ``_1776_`` as its
    replacement.
    """

    image_name = (
        instance.get("image")
        or instance.get("image_name")
        or instance.get("docker_image")
    )
    if image_name:
        return str(image_name)
    instance_id = str(instance["instance_id"])
    docker_id = instance_id.replace("__", "_1776_")
    return f"docker.io/swebench/sweb.eval.x86_64.{docker_id}:latest".lower()


# A descriptive alias is useful to callers that do not know the historical
# upstream function name.
resolve_swebench_docker_image = get_swebench_docker_image_name


def _parse_slice_spec(slice_spec: str | slice | None) -> slice | None:
    """Parse a Python-style ``start:stop:step`` slice specification."""

    if slice_spec is None or slice_spec == "":
        return None
    if isinstance(slice_spec, slice):
        return slice_spec
    if not isinstance(slice_spec, str):
        raise TypeError("slice_spec must be a string, slice, or None")
    values = slice_spec.strip().split(":")
    if len(values) > 3:
        raise ValueError(f"invalid slice specification: {slice_spec!r}")

    def parse(value: str) -> int | None:
        value = value.strip()
        return None if value == "" else int(value)

    parsed = [parse(value) for value in values]
    # ``slice(5)`` means ``[5:]`` and is useful for a quick resume selection.
    if len(parsed) == 1:
        return slice(parsed[0], None, None)
    return slice(*parsed)


def filter_instances(
    instances: Iterable[Mapping[str, Any]],
    *,
    filter_spec: str = "",
    slice_spec: str | slice | None = "",
    shuffle: bool = False,
    seed: int = 42,
) -> list[dict[str, Any]]:
    """Filter, slice, and optionally deterministically shuffle instances.

    Shuffling starts from an instance-id-sorted copy.  This makes the result
    independent of dataset iteration order while retaining the ordering used
    by the upstream mini-SWE-agent runner.  The input objects are copied into
    a new list and are never mutated.
    """

    selected = [dict(instance) for instance in instances]
    if shuffle:
        selected.sort(key=lambda item: str(item["instance_id"]))
        random.Random(seed).shuffle(selected)

    if filter_spec:
        pattern = re.compile(filter_spec)
        selected = [
            instance
            for instance in selected
            if pattern.match(str(instance["instance_id"]))
        ]

    parsed_slice = _parse_slice_spec(slice_spec)
    if parsed_slice is not None:
        selected = selected[parsed_slice]
    return selected


def _default_dataset_loader(dataset_path: str, *, split: str) -> Iterable[Mapping[str, Any]]:
    """Load a dataset lazily, keeping ``datasets`` an optional dependency."""

    try:
        from datasets import load_dataset
    except ImportError as exc:  # pragma: no cover - exercised by integration users
        raise ImportError(
            "Loading SWE-bench datasets requires the optional 'datasets' package; "
            "pass dataset_loader=... to use a custom loader"
        ) from exc
    return load_dataset(dataset_path, split=split)


def load_swebench_dataset(
    subset: str = "lite",
    split: str = "dev",
    *,
    dataset_loader: Callable[..., Iterable[Mapping[str, Any]]] | None = None,
    dataset_mapping: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Load one SWE-bench subset using an injectable dataset loader.

    ``subset`` may be an alias in :data:`DATASET_MAPPING` or a Hugging Face
    dataset path.  The loader receives ``(dataset_path, split=split)`` and may
    return a Hugging Face Dataset, a list, or any other iterable of mappings.
    """

    mapping = DATASET_MAPPING if dataset_mapping is None else dataset_mapping
    dataset_path = mapping.get(subset, subset)
    loader = dataset_loader or _default_dataset_loader
    return [dict(instance) for instance in loader(dataset_path, split=split)]


# Backwards/forwards-friendly aliases for callers that prefer an explicit
# ``load_dataset`` name.  They are intentionally lazy in the same way.
load_dataset = load_swebench_dataset


def update_preds_file(
    output_path: str | Path,
    instance_id: str,
    model_name: str,
    result: Any,
) -> dict[str, str]:
    """Compatibility helper that atomically updates a keyed predictions file."""

    prediction = _standard_prediction(instance_id, result, model_name)
    PredictionStore(output_path).update(prediction)
    return prediction


def remove_from_preds_file(output_path: str | Path, instance_id: str) -> None:
    """Compatibility helper that removes one prediction if present."""

    PredictionStore(output_path).remove(instance_id)


def _atomic_write_text(path: Path, text: str) -> None:
    """Write *text* beside *path* and atomically replace the destination."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        # If os.replace succeeded this is harmless; if serialization or fsync
        # failed, no partial target is left behind.
        temporary_path.unlink(missing_ok=True)


def _json_default(value: Any) -> str:
    """Best-effort serializer for provider-specific trajectory objects."""

    return repr(value)


class PredictionStore:
    """Thread-safe, atomically persisted SWE-bench predictions.

    A store always uses a keyed JSON file as its canonical source.  The
    ``export_jsonl`` method writes a JSONL representation on demand, allowing
    the same run to feed either the standard harness or line-oriented tools.
    Separate ``PredictionStore`` instances pointing at the same path share a
    process-local lock, which is important when callers construct one store
    in each worker.
    """

    _locks_guard = threading.Lock()
    _locks: dict[str, threading.RLock] = {}

    def __init__(self, path: str | Path):
        self.path = Path(path)
        key = str(self.path.expanduser().resolve())
        with self._locks_guard:
            self._lock = self._locks.setdefault(key, threading.RLock())

    def read(self) -> dict[str, dict[str, Any]]:
        """Read the current keyed predictions, returning a defensive copy."""

        with self._lock:
            if not self.path.exists():
                return {}
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, Mapping):
                return {str(key): dict(value) for key, value in raw.items()}
            # Being permissive here makes it possible to resume from a JSONL
            # conversion that was accidentally written as a JSON array.
            if isinstance(raw, list):
                result: dict[str, dict[str, Any]] = {}
                for value in raw:
                    if isinstance(value, Mapping) and value.get("instance_id") is not None:
                        result[str(value["instance_id"])] = dict(value)
                return result
            raise ValueError(f"prediction file must contain an object: {self.path}")

    def get(self, instance_id: str) -> dict[str, Any] | None:
        return self.read().get(str(instance_id))

    def __contains__(self, instance_id: object) -> bool:
        return str(instance_id) in self.read()

    def update(self, prediction: Mapping[str, Any]) -> dict[str, Any]:
        """Atomically upsert one standard prediction and return it."""

        if prediction.get("instance_id") is None:
            raise ValueError("prediction must include instance_id")
        value = dict(prediction)
        instance_id = str(value["instance_id"])
        value["instance_id"] = instance_id
        with self._lock:
            data = self.read()
            data[instance_id] = value
            _atomic_write_text(
                self.path,
                json.dumps(data, indent=2, ensure_ascii=False, default=_json_default) + "\n",
            )
        return value

    def remove(self, instance_id: str) -> None:
        """Atomically remove one prediction if it exists."""

        with self._lock:
            data = self.read()
            if str(instance_id) not in data:
                return
            del data[str(instance_id)]
            _atomic_write_text(
                self.path,
                json.dumps(data, indent=2, ensure_ascii=False, default=_json_default) + "\n",
            )

    def export_json(self, path: str | Path | None = None) -> Path:
        """Export keyed JSON to *path* (or rewrite the canonical path)."""

        target = self.path if path is None else Path(path)
        with self._lock:
            data = self.read()
            _atomic_write_text(
                target,
                json.dumps(data, indent=2, ensure_ascii=False, default=_json_default) + "\n",
            )
        return target

    def export_jsonl(self, path: str | Path) -> Path:
        """Export current predictions as one standard JSON object per line."""

        target = Path(path)
        with self._lock:
            data = self.read()
            text = "".join(
                json.dumps(value, ensure_ascii=False, default=_json_default) + "\n"
                for value in data.values()
            )
            _atomic_write_text(target, text)
        return target


def _section(config: Any, name: str, default: Any = None) -> Any:
    if config is None:
        return default
    if isinstance(config, Mapping):
        return config.get(name, default)
    return getattr(config, name, default)


def _factory_call(factory: Callable[..., Any], role: str, values: Mapping[str, Any]) -> Any:
    """Call a user factory while accommodating common compact signatures.

    Factories can use named arguments (recommended) or positional arguments:

    ``model_factory(config)``, ``environment_factory(instance, image)``, and
    ``agent_factory(model, environment, config)`` are all accepted in addition
    to the fully named form.
    """

    try:
        signature = inspect.signature(factory)
    except (TypeError, ValueError):  # builtins and unusual callables
        return factory(**dict(values))

    parameters = list(signature.parameters.values())
    accepts_var_kwargs = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters
    )
    if accepts_var_kwargs:
        return factory(**dict(values))

    kwargs = {
        name: value
        for name, value in values.items()
        if any(
            parameter.name == name
            and parameter.kind
            in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
            for parameter in parameters
        )
    }
    positional_only = [
        parameter
        for parameter in parameters
        if parameter.kind is inspect.Parameter.POSITIONAL_ONLY
    ]
    # For unknown positional names, use the role's conventional ordering.
    role_order = {
        "model": ("config", "instance"),
        "environment": ("instance", "image", "config"),
        "agent": ("model", "environment", "config", "instance"),
    }[role]
    positional_values = [values[name] for name in role_order if name in values]

    # A named positional-or-keyword parameter that was not in ``values`` is
    # optional or will produce the same clear TypeError as the factory itself.
    # Positional-only parameters cannot be passed in kwargs.
    if positional_only:
        positional = []
        for index, parameter in enumerate(positional_only):
            if parameter.name in values:
                positional.append(values[parameter.name])
            elif index < len(positional_values):
                positional.append(positional_values[index])
            elif parameter.default is inspect.Parameter.empty:
                raise TypeError(f"missing required factory argument: {parameter.name}")
        return factory(*positional, **kwargs)

    # Handle short lambdas with an uninformative parameter name (e.g. ``lambda
    # cfg: ...``) by supplying conventional positional values.  Named
    # parameters stay keyword-based so order is never surprising.
    required_unknown = [
        parameter
        for parameter in parameters
        if parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
        and parameter.default is inspect.Parameter.empty
        and parameter.name not in kwargs
        and parameter.name not in values
    ]
    if required_unknown:
        positional = positional_values[: len(required_unknown)]
        if len(positional) < len(required_unknown):
            raise TypeError(f"missing required factory argument: {required_unknown[0].name}")
        return factory(*positional, **kwargs)
    return factory(**kwargs)


def _default_model_factory(*, config: Any = None, instance: Mapping[str, Any] | None = None) -> Any:
    from mini_agent.model import Model

    model_config = _section(config, "model")
    if model_config is None:
        return Model()
    # ModelConfig is a pydantic object in normal use.  For a plain mapping,
    # construct it here so callers can pass a small test/CLI config dictionary.
    if isinstance(model_config, Mapping):
        from mini_agent.config import ModelConfig

        model_config = ModelConfig(**model_config)
    return Model(model_config)


def _default_environment_factory(
    *,
    instance: Mapping[str, Any],
    image: str,
    config: Any = None,
) -> Any:
    environment_config = _section(config, "environment")
    environment_type = _section(environment_config, "type") or _section(
        environment_config, "environment_class"
    ) or "docker"
    kwargs: dict[str, Any] = {}
    if environment_config is not None:
        if isinstance(environment_config, Mapping):
            # ``get_environment`` expects the typed EnvironmentConfig used by
            # the rest of this project.  ``environment_class`` is an upstream
            # runner spelling and is not a field on that model.
            from mini_agent.config import EnvironmentConfig

            config_values = {
                key: value
                for key, value in environment_config.items()
                if key != "environment_class"
            }
            kwargs["config"] = EnvironmentConfig(**config_values)
        else:
            kwargs["config"] = environment_config
    if environment_type == "docker":
        kwargs["image"] = image
    return get_environment(environment_type, **kwargs)


def get_sb_environment(
    config: Any,
    instance: Mapping[str, Any],
    *,
    environment_factory: Callable[..., Any] | None = None,
) -> Any:
    """Build the per-instance environment without mutating shared config.

    This small helper mirrors the upstream runner's public API.  A caller can
    inject ``environment_factory`` for tests or alternate runtimes.  If the
    config contains ``run.env_startup_command``, it is executed after startup;
    both legacy string results and structured ``{"returncode": ...}``
    environment results are accepted.
    """

    factory = environment_factory or _default_environment_factory
    image = get_swebench_docker_image_name(instance)
    environment = _factory_call(
        factory,
        "environment",
        {"instance": dict(instance), "image": image, "config": config},
    )
    try:
        run_config = _section(config, "run", {}) or {}
        startup_command = _section(run_config, "env_startup_command")
        if startup_command:
            from mini_agent.config import render_template

            command = render_template(str(startup_command), **dict(instance))
            output = environment.execute(command)
            if isinstance(output, Mapping) and output.get("returncode", 0) != 0:
                raise RuntimeError(f"Error executing startup command: {output}")
        return environment
    except BaseException:
        cleanup = getattr(environment, "cleanup", None)
        if callable(cleanup):
            try:
                cleanup()
            except Exception:
                pass
        raise


def _default_agent_factory(
    *,
    model: Any,
    environment: Any,
    config: Any = None,
    instance: Mapping[str, Any] | None = None,
) -> Any:
    from mini_agent.agent import Agent

    if config is None:
        return Agent(model, environment)
    # A Config object is accepted directly.  Plain dictionaries are useful in
    # tests but may not contain all required prompt/tool settings, so let the
    # Agent use its normal default in that case.
    from mini_agent.config import Config

    if isinstance(config, Config):
        return Agent(model, environment, config=config)
    return Agent(model, environment)


def _model_name(model: Any, config: Any = None, explicit: str | None = None) -> str:
    if explicit is not None:
        return str(explicit)
    model_config = getattr(model, "config", None)
    name = getattr(model_config, "model_name", None)
    if name:
        return str(name)
    configured = _section(_section(config, "model"), "model_name")
    return str(configured or getattr(model, "model_name", "unknown"))


def _task_from_instance(instance: Mapping[str, Any]) -> str:
    for key in ("problem_statement", "task", "prompt"):
        value = instance.get(key)
        if value is not None:
            return str(value)
    raise KeyError("SWE-bench instance must contain problem_statement")


def _safe_instance_id(instance: Mapping[str, Any]) -> str:
    value = instance.get("instance_id")
    if value is None or str(value) == "":
        raise KeyError("SWE-bench instance must contain instance_id")
    return str(value)


def _trajectory_instance(instance: Mapping[str, Any]) -> dict[str, Any]:
    """Return public instance metadata safe to persist beside a trajectory."""

    return {
        key: instance[key]
        for key in TRAJECTORY_INSTANCE_FIELDS
        if key in instance
    }


def _trajectory_path(output_dir: Path, instance_id: str) -> Path:
    # SWE-bench ids do not contain path separators.  Rejecting them rather
    # than silently writing outside output_dir protects custom datasets.
    if Path(instance_id).name != instance_id or instance_id in {".", ".."}:
        raise ValueError(f"instance_id cannot contain path separators: {instance_id!r}")
    return output_dir / instance_id / f"{instance_id}.traj.json"


def _progress(progress_manager: Any, method: str, *args: Any) -> None:
    callback = getattr(progress_manager, method, None) if progress_manager is not None else None
    if callback is not None:
        try:
            callback(*args)
        except Exception:
            # Progress output must never turn an otherwise valid benchmark run
            # into a failed instance.
            pass


def _write_trajectory(
    path: Path,
    instance: Mapping[str, Any],
    agent: Any,
    result: Mapping[str, Any],
    *,
    exception_info: Mapping[str, Any] | None = None,
) -> None:
    data: dict[str, Any] = {}
    serializer = getattr(agent, "serialize", None) if agent is not None else None
    if callable(serializer):
        try:
            serialized = serializer()
            if isinstance(serialized, Mapping):
                data.update(serialized)
        except Exception as exc:
            data["serialize_error"] = repr(exc)
    if not data and isinstance(result.get("messages"), list):
        data["messages"] = result["messages"]

    info = dict(data.get("info") or {})
    info.update(
        {
            "instance_id": str(instance["instance_id"]),
            "exit_status": result.get("exit_status", "error"),
            "submission": result.get("submission", ""),
        }
    )
    if exception_info:
        info.update(exception_info)
    data["info"] = info
    data["instance_id"] = str(instance["instance_id"])
    data["instance"] = _trajectory_instance(instance)
    data.setdefault("trajectory_format", "mini-agent-0.2")
    save_trajectory_data(path, data)


def _run_agent(agent: Any, task: str, trajectory: Path) -> Any:
    """Run an agent, enabling live event journalling when its API supports it."""

    run_method = agent.run
    try:
        parameters = inspect.signature(run_method).parameters.values()
    except (TypeError, ValueError):
        parameters = ()
    accepts_output = any(
        parameter.name == "output"
        or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )
    if accepts_output:
        return run_method(task, output=trajectory)
    return run_method(task)


def _standard_prediction(
    instance_id: str,
    submission: Any,
    model_name: str,
) -> dict[str, str]:
    return {
        "model_name_or_path": str(model_name),
        "instance_id": instance_id,
        "model_patch": "" if submission is None else str(submission),
    }


def _looks_like_unified_diff(value: str) -> bool:
    """Return whether *value* looks like a git/unified source patch."""

    return "diff --git " in value or ("--- a/" in value and "+++ b/" in value)


def _execution_output(result: Any) -> str:
    if isinstance(result, Mapping):
        return str(result.get("output", ""))
    return str(result or "")


def collect_model_patch(submission: Any, environment: Any) -> str:
    """Return a validated patch, falling back to the environment's git diff.

    Models occasionally call ``submit`` with a prose summary even when the
    benchmark prompt asks for a patch.  The working tree is authoritative, so
    the runner attempts to collect its diff before declaring the submission
    invalid.
    """

    submitted = "" if submission is None else str(submission)
    if _looks_like_unified_diff(submitted):
        return submitted
    if environment is None:
        return ""
    try:
        generated = _execution_output(
            environment.execute("git diff --binary --no-ext-diff", timeout=30)
        )
    except Exception:
        return ""
    return generated if _looks_like_unified_diff(generated) else ""


class SWEbenchRunner:
    """Run SWE-bench instances with isolated model/environment/agent objects."""

    def __init__(
        self,
        output_dir: str | Path,
        *,
        config: Any = None,
        dataset_loader: Callable[..., Iterable[Mapping[str, Any]]] | None = None,
        model_factory: Callable[..., Any] | None = None,
        environment_factory: Callable[..., Any] | None = None,
        agent_factory: Callable[..., Any] | None = None,
        prediction_store: PredictionStore | None = None,
        model_name: str | None = None,
        workers: int = 1,
        seed: int = 42,
        status_path: str | Path | None = None,
        progress_manager: Any = None,
    ) -> None:
        if workers < 1:
            raise ValueError("workers must be at least 1")
        self.output_dir = Path(output_dir)
        self.config = config
        self.dataset_loader = dataset_loader
        self.model_factory = model_factory or _default_model_factory
        self.environment_factory = environment_factory or _default_environment_factory
        self.agent_factory = agent_factory or _default_agent_factory
        self.predictions = prediction_store or PredictionStore(self.output_dir / "preds.json")
        self.model_name = model_name
        self.workers = workers
        self.seed = seed
        self.status_path = Path(status_path) if status_path else self.output_dir / "statuses.json"
        self._status_lock = threading.RLock()
        self.progress_manager = progress_manager

    def load_instances(self, subset: str = "lite", split: str = "dev") -> list[dict[str, Any]]:
        return load_swebench_dataset(
            subset,
            split,
            dataset_loader=self.dataset_loader,
        )

    def _read_statuses(self) -> dict[str, dict[str, Any]]:
        with self._status_lock:
            if not self.status_path.exists():
                return {}
            raw = json.loads(self.status_path.read_text(encoding="utf-8"))
            if not isinstance(raw, Mapping):
                return {}
            return {str(key): dict(value) for key, value in raw.items()}

    def _write_status(self, instance_id: str, result: Mapping[str, Any]) -> None:
        with self._status_lock:
            statuses = self._read_statuses()
            statuses[str(instance_id)] = {
                "instance_id": str(instance_id),
                "exit_status": result.get("exit_status", "error"),
                "submission": result.get("submission", ""),
                "exception": result.get("exception"),
            }
            _atomic_write_text(
                self.status_path,
                json.dumps(statuses, indent=2, ensure_ascii=False, default=_json_default) + "\n",
            )

    def _is_failed(self, instance_id: str, statuses: Mapping[str, Mapping[str, Any]]) -> bool:
        status = statuses.get(str(instance_id), {}).get("exit_status")
        if status is not None:
            return status != "submitted"
        trajectory = _trajectory_path(self.output_dir, str(instance_id))
        if trajectory.exists():
            try:
                data = json.loads(trajectory.read_text(encoding="utf-8"))
                return (data.get("info") or {}).get("exit_status") != "submitted"
            except (OSError, ValueError, TypeError):
                return True
        # A manually-created standard prediction has no failure metadata; it
        # is considered completed for ordinary resume mode.
        return False

    def process_instance(self, instance: Mapping[str, Any]) -> dict[str, Any]:
        """Run one instance, persist its trajectory and prediction, and isolate errors."""

        instance = dict(instance)
        instance_id = _safe_instance_id(instance)
        task = _task_from_instance(instance)
        trajectory = _trajectory_path(self.output_dir, instance_id)
        _progress(self.progress_manager, "on_instance_start", instance_id)

        model = None
        environment = None
        agent = None
        result: dict[str, Any] = {
            "instance_id": instance_id,
            "exit_status": "error",
            "submission": "",
        }
        exception_info: dict[str, Any] = {}
        model_name = self.model_name or _model_name(None, self.config)
        try:
            model = _factory_call(
                self.model_factory,
                "model",
                {"config": self.config, "instance": instance},
            )
            model_name = _model_name(model, self.config, self.model_name)
            environment = get_sb_environment(
                self.config,
                instance,
                environment_factory=self.environment_factory,
            )
            agent = _factory_call(
                self.agent_factory,
                "agent",
                {
                    "model": model,
                    "environment": environment,
                    "config": self.config,
                    "instance": instance,
                },
            )
            raw_result = _run_agent(agent, task, trajectory)
            if isinstance(raw_result, Mapping):
                result.update(raw_result)
            else:
                result["submission"] = raw_result or ""
            result["instance_id"] = instance_id
            result["submission"] = result.get("submission") or ""
            result["exit_status"] = result.get("exit_status") or "error"
            if result["exit_status"] == "submitted":
                patch = collect_model_patch(result["submission"], environment)
                if patch:
                    result["submission"] = patch
                else:
                    result["exit_status"] = "invalid_patch"
                    result["exception"] = (
                        "Agent submitted no valid unified diff and git diff was empty"
                    )
        except KeyboardInterrupt:
            # Preserve normal Ctrl-C semantics for the batch executor.  The
            # ``finally`` block still cleans the environment and writes files.
            result.update({"exit_status": "interrupted", "submission": ""})
            raise
        except Exception as exc:
            result.update({"exit_status": type(exc).__name__, "submission": ""})
            result["exception"] = str(exc)
            exception_info = {
                "exception_type": type(exc).__name__,
                "exception_str": str(exc),
                "traceback": traceback.format_exc(),
            }
        finally:
            try:
                _write_trajectory(
                    trajectory,
                    instance,
                    agent,
                    result,
                    exception_info=exception_info,
                )
            except Exception as exc:
                # A trajectory error is recorded in the result but should not
                # prevent cleanup or the standard prediction from being saved.
                result.setdefault("exception", str(exc))
                result.setdefault("trajectory_error", repr(exc))
            if environment is not None:
                try:
                    cleanup = getattr(environment, "cleanup", None)
                    if callable(cleanup):
                        cleanup()
                except Exception as exc:
                    result.setdefault("cleanup_error", repr(exc))
            if model is not None:
                try:
                    close = getattr(model, "close", None)
                    if callable(close):
                        close()
                except Exception as exc:
                    result.setdefault("model_close_error", repr(exc))

            prediction = _standard_prediction(
                instance_id,
                result.get("submission", ""),
                model_name,
            )
            result["prediction"] = prediction
            try:
                self.predictions.update(prediction)
            finally:
                self._write_status(instance_id, result)
            _progress(self.progress_manager, "on_instance_end", instance_id, result.get("exit_status"))
        return result

    def run(
        self,
        instances: Iterable[Mapping[str, Any]] | None = None,
        *,
        subset: str = "lite",
        split: str = "dev",
        filter_spec: str = "",
        slice_spec: str | slice | None = "",
        shuffle: bool = False,
        seed: int | None = None,
        workers: int | None = None,
        redo_existing: bool = False,
        retry_failed: bool = False,
        jsonl_path: str | Path | None = None,
    ) -> list[dict[str, Any]]:
        """Run a selected set of instances, returning results in input order."""

        if instances is None:
            instances = self.load_instances(subset, split)
        selected = filter_instances(
            instances,
            filter_spec=filter_spec,
            slice_spec=slice_spec,
            shuffle=shuffle,
            seed=self.seed if seed is None else seed,
        )
        existing = self.predictions.read()
        statuses = self._read_statuses()
        pending: list[dict[str, Any]] = []
        for instance in selected:
            instance_id = _safe_instance_id(instance)
            if redo_existing or instance_id not in existing:
                pending.append(instance)
            elif retry_failed and self._is_failed(instance_id, statuses):
                pending.append(instance)

        max_workers = self.workers if workers is None else workers
        if max_workers < 1:
            raise ValueError("workers must be at least 1")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        if not pending:
            if jsonl_path is not None:
                self.predictions.export_jsonl(jsonl_path)
            return []

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
        futures: dict[concurrent.futures.Future[dict[str, Any]], str] = {}
        results: dict[str, dict[str, Any]] = {}
        try:
            futures = {
                executor.submit(self.process_instance, instance): _safe_instance_id(instance)
                for instance in pending
            }
            try:
                for future in concurrent.futures.as_completed(futures):
                    instance_id = futures[future]
                    try:
                        results[instance_id] = future.result()
                    except KeyboardInterrupt:
                        raise
                    except Exception as exc:
                        # The normal process path isolates exceptions, but a
                        # custom future can still fail outside that path.
                        result = {
                            "instance_id": instance_id,
                            "exit_status": type(exc).__name__,
                            "submission": "",
                            "exception": str(exc),
                            "prediction": _standard_prediction(
                                instance_id, "", self.model_name or "unknown"
                            ),
                        }
                        results[instance_id] = result
                        self.predictions.update(result["prediction"])
                        self._write_status(instance_id, result)
            except KeyboardInterrupt:
                for future in futures:
                    if not future.running() and not future.done():
                        future.cancel()
                executor.shutdown(wait=False, cancel_futures=True)
                raise
            else:
                executor.shutdown(wait=True)
        except BaseException:
            # If submit itself failed, make sure worker threads are not left
            # holding resources.  Running process_instance calls still execute
            # their own ``finally`` cleanup blocks.
            if not getattr(executor, "_shutdown", False):
                executor.shutdown(wait=False, cancel_futures=True)
            raise

        if jsonl_path is not None:
            self.predictions.export_jsonl(jsonl_path)
        return [results[_safe_instance_id(instance)] for instance in pending if _safe_instance_id(instance) in results]

    run_batch = run


def process_instance(
    instance: Mapping[str, Any],
    output_dir: str | Path,
    config: Any = None,
    progress_manager: Any = None,
    *,
    model_factory: Callable[..., Any] | None = None,
    environment_factory: Callable[..., Any] | None = None,
    agent_factory: Callable[..., Any] | None = None,
    prediction_store: PredictionStore | None = None,
    model_name: str | None = None,
) -> dict[str, Any]:
    """Convenience wrapper for processing one instance.

    ``progress_manager`` is accepted for parity with the upstream runner;
    progress callbacks are intentionally best-effort and never affect a run.
    """

    runner = SWEbenchRunner(
        output_dir,
        config=config,
        model_factory=model_factory,
        environment_factory=environment_factory,
        agent_factory=agent_factory,
        prediction_store=prediction_store,
        model_name=model_name,
        progress_manager=progress_manager,
    )
    result = runner.process_instance(instance)
    return result


def run_batch(
    instances: Iterable[Mapping[str, Any]] | None = None,
    output_dir: str | Path = "results",
    *,
    subset: str = "lite",
    split: str = "dev",
    config: Any = None,
    dataset_loader: Callable[..., Iterable[Mapping[str, Any]]] | None = None,
    model_factory: Callable[..., Any] | None = None,
    environment_factory: Callable[..., Any] | None = None,
    agent_factory: Callable[..., Any] | None = None,
    model_name: str | None = None,
    workers: int = 1,
    seed: int = 42,
    filter_spec: str = "",
    slice_spec: str | slice | None = "",
    shuffle: bool = False,
    redo_existing: bool = False,
    retry_failed: bool = False,
    jsonl_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Functional convenience wrapper around :class:`SWEbenchRunner`."""

    return SWEbenchRunner(
        output_dir,
        config=config,
        dataset_loader=dataset_loader,
        model_factory=model_factory,
        environment_factory=environment_factory,
        agent_factory=agent_factory,
        model_name=model_name,
        workers=workers,
        seed=seed,
    ).run(
        instances,
        subset=subset,
        split=split,
        filter_spec=filter_spec,
        slice_spec=slice_spec,
        shuffle=shuffle,
        redo_existing=redo_existing,
        retry_failed=retry_failed,
        jsonl_path=jsonl_path,
    )


__all__ = [
    "DATASET_MAPPING",
    "PredictionStore",
    "SWEbenchRunner",
    "collect_model_patch",
    "filter_instances",
    "get_sb_environment",
    "get_swebench_docker_image_name",
    "load_dataset",
    "load_swebench_dataset",
    "process_instance",
    "remove_from_preds_file",
    "resolve_swebench_docker_image",
    "run_batch",
    "update_preds_file",
]
