"""Pure dataset selection and image-resolution helpers for SWE-bench."""

from __future__ import annotations

import random
import re
from collections.abc import Callable, Iterable, Mapping
from typing import Any


# Keep this mapping below the runner's public API, rather than in the CLI, so
# Python and command-line callers resolve exactly the same aliases.
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


def get_swebench_docker_image_name(instance: Mapping[str, Any]) -> str:
    """Return the Docker image associated with a SWE-bench instance.

    Dataset revisions have used ``image``, ``image_name``, and
    ``docker_image``.  When none is present, derive the conventional image
    name from ``instance_id`` and replace double underscores with the token
    expected by the SWE-bench image registry.
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
    """Return copied instances after deterministic shuffle, filter, and slice.

    Shuffling starts from an instance-id-sorted copy, so the result is stable
    even when two dataset providers yield the same rows in different orders.
    The caller's mappings are never mutated.
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


def _default_dataset_loader(
    dataset_path: str,
    *,
    split: str,
) -> Iterable[Mapping[str, Any]]:
    """Load through the optional ``datasets`` dependency only when invoked."""

    try:
        from datasets import load_dataset
    except ImportError as exc:  # pragma: no cover - integration-only failure
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
    """Load an alias or dataset path and copy rows into plain dictionaries.

    ``subset`` may be a key in :data:`DATASET_MAPPING` or a complete dataset
    path.  An injected loader keeps tests and custom data sources independent
    of the optional Hugging Face ``datasets`` package.
    """

    mapping = DATASET_MAPPING if dataset_mapping is None else dataset_mapping
    dataset_path = mapping.get(subset, subset)
    loader = dataset_loader or _default_dataset_loader
    return [dict(instance) for instance in loader(dataset_path, split=split)]


load_dataset = load_swebench_dataset


__all__ = [
    "DATASET_MAPPING",
    "filter_instances",
    "get_swebench_docker_image_name",
    "load_dataset",
    "load_swebench_dataset",
    "resolve_swebench_docker_image",
]
