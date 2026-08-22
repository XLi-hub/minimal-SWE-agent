"""Command-line entry point for SWE-bench generation runs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from mini_agent.benchmarks.swebench import SWEbenchRunner, filter_instances
from mini_agent.config import UNSET, build_config


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="minimal-swebench",
        description="Run minimal-SWE-agent on SWE-bench instances.",
    )
    parser.add_argument("--subset", default="lite", help="Dataset alias or Hugging Face dataset path")
    parser.add_argument("--split", default="dev", help="Dataset split")
    parser.add_argument("-i", "--instance", default=None, help="Instance id or sorted numeric index")
    parser.add_argument("--filter", default="", help="Regex matched against instance_id")
    parser.add_argument("--slice", dest="slice_spec", default="", help="Python slice, for example 0:10")
    parser.add_argument("--shuffle", action="store_true", help="Deterministically shuffle before slicing")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("-w", "--workers", type=int, default=1)
    parser.add_argument("-o", "--output", default="swebench-results")
    parser.add_argument("-m", "--model", default=None)
    parser.add_argument("-c", "--config", action="append", default=[], metavar="SPEC")
    parser.add_argument("--redo-existing", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument(
        "--jsonl",
        default=None,
        help="JSONL prediction path (default: <output>/preds.jsonl)",
    )
    return parser.parse_args(argv)


def _select_instance(instances: list[dict], instance_spec: str) -> list[dict]:
    by_id = {str(instance["instance_id"]): instance for instance in instances}
    if instance_spec.isnumeric():
        instance_id = sorted(by_id)[int(instance_spec)]
        return [by_id[instance_id]]
    try:
        return [by_id[instance_spec]]
    except KeyError as exc:
        raise ValueError(f"Unknown SWE-bench instance: {instance_spec}") from exc


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    config = build_config(
        ["benchmarks/swebench", *args.config],
        cli_overrides={
            "model": {"model_name": args.model if args.model is not None else UNSET},
        },
    )
    output_dir = Path(args.output)
    runner = SWEbenchRunner(
        output_dir,
        config=config,
        workers=args.workers,
        seed=args.seed,
        model_name=args.model,
    )
    instances = runner.load_instances(args.subset, args.split)
    if args.instance is not None:
        instances = _select_instance(instances, args.instance)
    else:
        instances = filter_instances(
            instances,
            filter_spec=args.filter,
            slice_spec=args.slice_spec,
            shuffle=args.shuffle,
            seed=args.seed,
        )

    jsonl_path = Path(args.jsonl) if args.jsonl else output_dir / "preds.jsonl"
    results = runner.run(
        instances,
        workers=args.workers,
        redo_existing=args.redo_existing,
        retry_failed=args.retry_failed,
        jsonl_path=jsonl_path,
    )
    counts: dict[str, int] = {}
    for result in results:
        status = str(result.get("exit_status", "error"))
        counts[status] = counts.get(status, 0) + 1
    print(json.dumps({"processed": len(results), "exit_statuses": counts}, indent=2))
    print(f"Predictions: {runner.predictions.path}")
    print(f"JSONL: {jsonl_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
