"""Benchmark runners.

The benchmark modules deliberately keep their dependencies optional.  Importing
``mini_agent.benchmarks`` does not import ``datasets`` or start a container;
those things are only needed when a runner is actually used.
"""

from mini_agent.benchmarks.swebench import (
    DATASET_MAPPING,
    PredictionStore,
    SWEbenchRunner,
    collect_model_patch,
    filter_instances,
    get_sb_environment,
    get_swebench_docker_image_name,
    load_swebench_dataset,
    process_instance,
    remove_from_preds_file,
    run_batch,
    update_preds_file,
)

__all__ = [
    "DATASET_MAPPING",
    "PredictionStore",
    "SWEbenchRunner",
    "collect_model_patch",
    "filter_instances",
    "get_sb_environment",
    "get_swebench_docker_image_name",
    "load_swebench_dataset",
    "process_instance",
    "remove_from_preds_file",
    "run_batch",
    "update_preds_file",
]
