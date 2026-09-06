"""Prediction normalization and atomic persistence for SWE-bench."""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from mini_agent.persistence import atomic_write_text


def _json_default(value: Any) -> str:
    """Best-effort serializer for provider-specific trajectory objects."""

    return repr(value)


def _standard_prediction(
    instance_id: str,
    submission: Any,
    model_name: str,
) -> dict[str, str]:
    """Normalize a model result to the three-field SWE-bench format."""

    return {
        "model_name_or_path": str(model_name),
        "instance_id": instance_id,
        "model_patch": "" if submission is None else str(submission),
    }


class PredictionStore:
    """Thread-safe, atomically persisted SWE-bench predictions.

    Store instances for the same resolved path share a process-local reentrant
    lock.  Keyed JSON is canonical and can be exported to JSONL on demand.
    """

    _locks_guard = threading.Lock()
    _locks: dict[str, threading.RLock] = {}

    def __init__(self, path: str | Path):
        self.path = Path(path)
        key = str(self.path.expanduser().resolve())
        with self._locks_guard:
            self._lock = self._locks.setdefault(key, threading.RLock())

    def read(self) -> dict[str, dict[str, Any]]:
        """Read current keyed predictions, returning a defensive copy."""

        with self._lock:
            if not self.path.exists():
                return {}
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, Mapping):
                return {str(key): dict(value) for key, value in raw.items()}
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
            atomic_write_text(
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
            atomic_write_text(
                self.path,
                json.dumps(data, indent=2, ensure_ascii=False, default=_json_default) + "\n",
            )

    def export_json(self, path: str | Path | None = None) -> Path:
        """Export keyed JSON to *path* (or rewrite the canonical path)."""

        target = self.path if path is None else Path(path)
        with self._lock:
            data = self.read()
            atomic_write_text(
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
            atomic_write_text(target, text)
        return target


def update_preds_file(
    output_path: str | Path,
    instance_id: str,
    model_name: str,
    result: Any,
) -> dict[str, str]:
    """Atomically update a keyed predictions file with a normalized result."""

    prediction = _standard_prediction(instance_id, result, model_name)
    PredictionStore(output_path).update(prediction)
    return prediction


def remove_from_preds_file(output_path: str | Path, instance_id: str) -> None:
    """Remove one prediction from a keyed predictions file if present."""

    PredictionStore(output_path).remove(instance_id)


__all__ = ["PredictionStore", "remove_from_preds_file", "update_preds_file"]
