"""Non-E2E tests for the low-disk SWE-bench wrapper."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from scripts.run_swebench_low_disk import (
    InstanceRef,
    _parse_args,
    aggregate_cost_stop_threshold,
    cleanup_instance_image,
    load_instance_file,
    run_instances,
    select_instances,
)


def _completed(command, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr=stderr)


def test_instance_files_accept_text_json_and_jsonl(tmp_path):
    text = tmp_path / "ids.txt"
    text.write_text("# comment\nrepo__one-1\n\nrepo__two-2 # inline\n")
    assert [ref.instance_id for ref in load_instance_file(text)] == [
        "repo__one-1",
        "repo__two-2",
    ]

    records = tmp_path / "records.json"
    records.write_text(
        json.dumps(
            {
                "instances": [
                    {"instance_id": "repo__three-3", "image": "custom/image:tag"},
                    "repo__four-4",
                ]
            }
        )
    )
    parsed = load_instance_file(records)
    assert parsed[0] == InstanceRef("repo__three-3", "custom/image:tag")
    assert parsed[1].resolved_image.endswith("repo_1776_four-4:latest")

    jsonl = tmp_path / "ids.jsonl"
    jsonl.write_text('{"instance_id":"repo__five-5"}\n"repo__six-6"\n')
    assert [ref.instance_id for ref in load_instance_file(jsonl)] == [
        "repo__five-5",
        "repo__six-6",
    ]


def test_select_instances_deduplicates_and_requires_explicit_selection(tmp_path):
    ids = tmp_path / "ids.txt"
    ids.write_text("repo__one-1\nrepo__two-2\n")
    refs = select_instances(["repo__one-1", "repo__three-3"], [ids])
    assert [ref.instance_id for ref in refs] == [
        "repo__one-1",
        "repo__three-3",
        "repo__two-2",
    ]
    with pytest.raises(ValueError, match="at least one"):
        select_instances()


def test_parser_accepts_provider_prices_cost_and_explicit_ids():
    args = _parse_args(
        [
            "--instance-id",
            "repo__one-1",
            "--output-dir",
            "out",
            "--model",
            "deepseek-flash",
            "--provider-url",
            "https://api.deepseek.com",
            "--api-key-env",
            "DEEPSEEK_API_KEY",
            "--input-price-per-1m",
            "0.27",
            "--cache-hit-price-per-1m",
            "0.014",
            "--output-price-per-1m",
            "1.10",
            "--cost-limit-usd",
            "0.5",
            "--pre-pull",
            "--pull-timeout",
            "2400",
            "--no-gc",
        ]
    )
    assert args.instance_ids == ["repo__one-1"]
    assert args.output == "out"
    assert args.provider == "https://api.deepseek.com"
    assert args.input_price == pytest.approx(0.27)
    assert args.cache_input_price == pytest.approx(0.014)
    assert args.output_price == pytest.approx(1.10)
    assert args.cost_limit == pytest.approx(0.5)
    assert args.pre_pull is True
    assert args.pull_timeout == 2400
    assert args.image_prune is False

    assert _parse_args(["--instance", "repo__one-1", "--output", "out"]).pre_pull is False
    assert (
        _parse_args(
            [
                "--instance",
                "repo__one-1",
                "--output",
                "out",
                "--pre-pull",
                "--no-pre-pull",
            ]
        ).pre_pull
        is False
    )


def test_cost_stop_threshold_is_per_instance():
    assert aggregate_cost_stop_threshold(3, 0.5) == 1.5
    assert aggregate_cost_stop_threshold(3, 0) is None
    assert aggregate_cost_stop_threshold(3, None) is None


def test_cleanup_removes_exact_image_and_prunes_only_after_clean_precheck():
    calls = []

    def runner(command, **kwargs):
        calls.append(list(command))
        if command[1:3] == ["image", "ls"]:
            return _completed(command, stdout="")
        return _completed(command)

    result = cleanup_instance_image(
        "docker.io/swebench/sweb.eval.x86_64.repo_1776_one-1:latest",
        docker_executable="docker-test",
        runner=runner,
    )

    assert calls == [
        [
            "docker-test",
            "image",
            "rm",
            "docker.io/swebench/sweb.eval.x86_64.repo_1776_one-1:latest",
        ],
        ["docker-test", "image", "ls", "--filter", "dangling=true", "--quiet"],
        ["docker-test", "image", "prune", "--force"],
    ]
    assert result.ok
    assert result.prune_returncode == 0
    assert all("system" not in part for call in calls for part in call)


def test_cleanup_skips_prune_when_dangling_images_exist(capsys):
    calls = []

    def runner(command, **kwargs):
        calls.append(list(command))
        if command[1:3] == ["image", "ls"]:
            return _completed(command, stdout="dangling-id\n")
        return _completed(command)

    result = cleanup_instance_image("some/image:tag", runner=runner)

    assert len(calls) == 2
    assert result.prune_returncode is None
    assert result.skipped_prune_reason == "dangling images present"
    assert "dangling images already exist" in capsys.readouterr().err


def test_dry_run_is_serial_and_never_executes_commands(tmp_path, capsys):
    called = []

    def runner(command, **kwargs):
        called.append(command)
        raise AssertionError("dry-run must not invoke subprocesses")

    results = run_instances(
        [InstanceRef("repo__one-1"), InstanceRef("repo__two-2")],
        output_dir=tmp_path / "out",
        model="deepseek-flash",
        provider="https://api.deepseek.com",
        api_key_env="DEEPSEEK_API_KEY",
        input_price=0.27,
        cache_input_price=0.014,
        output_price=1.1,
        cost_limit=0.5,
        pre_pull=True,
        image_prune=True,
        dry_run=True,
        runner=runner,
    )

    assert called == []
    assert [result["instance_id"] for result in results] == [
        "repo__one-1",
        "repo__two-2",
    ]
    output = capsys.readouterr().out
    assert output.index("repo__one-1") < output.index("repo__two-2")
    assert "mini_agent.benchmarks.cli" in output
    assert "mini_agent.benchmarks.evaluation" in output
    assert "docker pull docker.io/swebench/sweb.eval.x86_64.repo_1776_one-1:latest" in output
    assert "--instance repo__one-1" in output
    assert "--workers 1" in output
    assert "conda" not in output


def test_run_is_serial_generation_then_eval_then_exact_cleanup(tmp_path):
    calls = []

    def runner(command, **kwargs):
        calls.append(list(command))
        if command[0] == "docker-test":
            if command[1:3] == ["image", "ls"]:
                return _completed(command, stdout="dangling\n")
            return _completed(command)
        return _completed(command)

    results = run_instances(
        [InstanceRef("repo__one-1"), InstanceRef("repo__two-2")],
        output_dir=tmp_path / "out",
        docker_executable="docker-test",
        image_prune=True,
        python_executable="python-test",
        runner=runner,
    )

    assert [result["instance_id"] for result in results] == [
        "repo__one-1",
        "repo__two-2",
    ]
    assert [command[0] for command in calls[:2]] == ["python-test", "python-test"]
    # The generation/evaluation pair for the first id is completed before the
    # second id starts; cleanup then performs the exact image rm and precheck.
    first_generation, first_eval = calls[:2]
    assert "mini_agent.benchmarks.cli" in first_generation
    assert "--instance" in first_generation
    assert first_generation[first_generation.index("--instance") + 1] == "repo__one-1"
    assert "mini_agent.benchmarks.evaluation" in first_eval
    assert first_eval[first_eval.index("--instance") + 1] == "repo__one-1"
    first_cleanup = calls[2]
    assert first_cleanup[:3] == ["docker-test", "image", "rm"]
    assert first_cleanup[-1].endswith("repo_1776_one-1:latest")
    second_generation_index = next(
        index
        for index, command in enumerate(calls)
        if "repo__two-2" in command and "mini_agent.benchmarks.cli" in command
    )
    assert second_generation_index > 2
    assert (tmp_path / "out" / "low_disk_status.json").is_file()


def test_pre_pull_success_runs_before_generation_and_is_recorded(tmp_path):
    calls = []

    def runner(command, **kwargs):
        calls.append((list(command), kwargs))
        return _completed(command)

    result = run_instances(
        [InstanceRef("repo__one-1")],
        output_dir=tmp_path / "out",
        pre_pull=True,
        pull_timeout=2400,
        docker_executable="docker-test",
        image_prune=False,
        python_executable="python-test",
        runner=runner,
    )[0]

    assert calls[0][0] == [
        "docker-test",
        "pull",
        "docker.io/swebench/sweb.eval.x86_64.repo_1776_one-1:latest",
    ]
    assert calls[0][1]["timeout"] == 2400
    assert "mini_agent.benchmarks.cli" in calls[1][0]
    assert result["pre_pull_status"] == "pulled"
    assert result["pre_pull_returncode"] == 0


def test_pre_pull_failure_records_manifest_skips_api_and_still_cleans(tmp_path, capsys):
    calls = []

    def runner(command, **kwargs):
        calls.append(list(command))
        if command[0] == "docker-test" and command[1] == "pull":
            return _completed(command, returncode=17, stderr="pull failed")
        raise AssertionError("generation/evaluation must not run after pull failure")

    result = run_instances(
        [InstanceRef("repo__one-1")],
        output_dir=tmp_path / "out",
        pre_pull=True,
        docker_executable="docker-test",
        image_prune=False,
        python_executable="python-test",
        runner=runner,
    )[0]

    assert calls == [
        [
            "docker-test",
            "pull",
            "docker.io/swebench/sweb.eval.x86_64.repo_1776_one-1:latest",
        ],
        [
            "docker-test",
            "image",
            "rm",
            "docker.io/swebench/sweb.eval.x86_64.repo_1776_one-1:latest",
        ],
    ]
    assert result["pre_pull_status"] == "failed"
    assert result["pre_pull_returncode"] == 17
    assert result["generation_returncode"] is None
    assert result["generation_skipped"] == "pre-pull failed"
    assert result["evaluation_skipped"] == "pre-pull failed"
    manifest = json.loads((tmp_path / "out" / "low_disk_status.json").read_text())
    assert manifest["repo__one-1"]["pre_pull_status"] == "failed"
    assert "skipping generation and evaluation" in capsys.readouterr().err


def test_failure_still_removes_image_and_can_be_retried(tmp_path):
    calls = []

    def failing_runner(command, **kwargs):
        calls.append(list(command))
        if command[0] == "docker-test":
            return _completed(command)
        return _completed(command, returncode=7, stderr="generation failed")

    result = run_instances(
        [InstanceRef("repo__one-1")],
        output_dir=tmp_path / "out",
        docker_executable="docker-test",
        image_prune=False,
        python_executable="python-test",
        runner=failing_runner,
    )[0]
    assert result["generation_returncode"] == 7
    assert result["evaluation_skipped"]
    assert any(command[1:3] == ["image", "rm"] for command in calls)

    retry_calls = []

    def successful_runner(command, **kwargs):
        retry_calls.append(list(command))
        if command[0] == "docker-test":
            return _completed(command)
        return _completed(command)

    retry = run_instances(
        [InstanceRef("repo__one-1")],
        output_dir=tmp_path / "out",
        retry_failed=True,
        docker_executable="docker-test",
        image_prune=False,
        python_executable="python-test",
        runner=successful_runner,
    )[0]
    assert retry["generation_returncode"] == 0
    assert retry["evaluation_returncode"] == 0
    generation = next(command for command in retry_calls if "mini_agent.benchmarks.cli" in command)
    assert "--retry-failed" in generation
