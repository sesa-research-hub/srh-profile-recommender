#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
#
# Copyright 2026 Sesa Research Hub
#
# SRH Private AI - Experiment Executor

from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import statistics
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from srh.benchmark import stream_chat
from srh.scenarios.run_pack import evaluate_answer


EXECUTOR_VERSION = "0.1.0"
RESULT_SCHEMA = "srh.experiment-result.v1"


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None

    ordered = sorted(values)

    # Nearest-rank percentile.
    rank = max(
        1,
        math.ceil((pct / 100.0) * len(ordered)),
    )

    return ordered[rank - 1]


def metric_summary(
    values: list[float | int | None],
) -> dict[str, float] | None:
    clean = [
        float(value)
        for value in values
        if value is not None
    ]

    if not clean:
        return None

    return {
        "min": round(min(clean), 4),
        "median": round(statistics.median(clean), 4),
        "p95": round(percentile(clean, 95) or 0.0, 4),
        "p99": round(percentile(clean, 99) or 0.0, 4),
        "max": round(max(clean), 4),
    }


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(
        path.read_text(encoding="utf-8")
    )

    if not isinstance(data, dict):
        raise ValueError(
            f"{path} must contain a JSON object"
        )

    return data


def find_experiment(
    plan: dict[str, Any],
    experiment_id: str,
) -> dict[str, Any]:
    for experiment in plan.get("experiments", []):
        if experiment.get("experiment_id") == experiment_id:
            return experiment

    raise ValueError(
        f"experiment not found in plan: {experiment_id}"
    )


def prepare_request(
    base_request: str,
    cache_state: str,
    repetition: int,
    request_no: int,
) -> str:
    instance = (
        "[SRH REQUEST INSTANCE "
        f"{repetition:04d}-{request_no:04d}]"
    )

    # cold/warm measure an uncached request prefix.
    # warm differs because the runtime is prewarmed separately.
    if cache_state in {"cold", "warm"}:
        return instance + "\n" + base_request

    # Preserve the large shared prefix while making each request
    # distinct at the tail.
    if cache_state == "shared-prefix":
        return base_request + "\n\n" + instance

    return base_request


def make_config(
    request_text: str,
    max_tokens: int,
) -> dict[str, Any]:
    return {
        "mode": "no-think",
        "messages": [
            {
                "role": "user",
                "content": request_text,
            }
        ],
        "max_tokens": max_tokens,
        "sampling": {
            "temperature": 0.0,
        },
    }


def execute_one(
    *,
    barrier: threading.Barrier,
    base_url: str,
    model: str,
    request_text: str,
    ground_truth: dict[str, Any],
    max_tokens: int,
    seed: int,
    request_no: int,
) -> dict[str, Any]:
    barrier.wait()

    started = time.perf_counter()

    try:
        performance = stream_chat(
            base_url,
            model,
            make_config(
                request_text,
                max_tokens,
            ),
            seed,
        )

        quality = evaluate_answer(
            performance["answer"],
            ground_truth,
        )

        return {
            "request_no": request_no,
            "success": True,
            "worker_elapsed_seconds": round(
                time.perf_counter() - started,
                4,
            ),
            "performance": {
                key: value
                for key, value in performance.items()
                if key != "answer"
            },
            "quality": quality,
            "answer": performance["answer"],
        }

    except Exception as exc:
        return {
            "request_no": request_no,
            "success": False,
            "worker_elapsed_seconds": round(
                time.perf_counter() - started,
                4,
            ),
            "error": str(exc),
        }


def prewarm(
    *,
    base_url: str,
    model: str,
    request_text: str,
    max_tokens: int,
    seed: int,
    cache_state: str,
) -> dict[str, Any]:

    if cache_state == "warm":
        # Warm the runtime without caching the measured workload prefix.
        warmup_text = (
            "SRH runtime warmup. "
            "Rispondi esclusivamente con: OK"
        )

    elif cache_state == "shared-prefix":
        marker = "\nDOMANDA\n=======\n"

        if marker in request_text:
            prefix, _ = request_text.split(marker, 1)

            # Cache instructions + large context while deliberately
            # using a different query suffix.
            warmup_text = (
                prefix
                + marker
                + "Richiesta di inizializzazione SRH. "
                + "Rispondi esclusivamente con: OK"
            )
        else:
            warmup_text = request_text

    else:
        warmup_text = request_text

    return stream_chat(
        base_url,
        model,
        make_config(
            warmup_text,
            min(max_tokens, 32),
        ),
        seed,
    )


def execute_batch(
    *,
    base_url: str,
    model: str,
    base_request: str,
    ground_truth: dict[str, Any],
    experiment: dict[str, Any],
    repetition: int,
    seed: int,
) -> dict[str, Any]:
    concurrency = experiment["concurrency"]
    cache_state = experiment["cache_state"]
    max_tokens = experiment["request"]["output_tokens"]

    warmup = None

    if cache_state in {"warm", "shared-prefix"}:
        warmup_started = time.perf_counter()

        warmup_result = prewarm(
            base_url=base_url,
            model=model,
            request_text=base_request,
            max_tokens=max_tokens,
            seed=seed,
            cache_state=cache_state,
        )

        warmup = {
            "elapsed_seconds": round(
                time.perf_counter() - warmup_started,
                4,
            ),
            "ttft_seconds": warmup_result.get(
                "ttft_seconds"
            ),
            "prompt_tokens": warmup_result.get(
                "prompt_tokens"
            ),
        }

    barrier = threading.Barrier(concurrency)

    requests = [
        prepare_request(
            base_request,
            cache_state,
            repetition,
            request_no,
        )
        for request_no in range(1, concurrency + 1)
    ]

    batch_started = time.perf_counter()

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=concurrency
    ) as pool:
        futures = [
            pool.submit(
                execute_one,
                barrier=barrier,
                base_url=base_url,
                model=model,
                request_text=request_text,
                ground_truth=ground_truth,
                max_tokens=max_tokens,
                seed=seed,
                request_no=request_no,
            )
            for request_no, request_text
            in enumerate(requests, start=1)
        ]

        results = [
            future.result()
            for future in futures
        ]

    batch_elapsed = time.perf_counter() - batch_started

    successful = [
        result
        for result in results
        if result.get("success")
    ]

    failed = len(results) - len(successful)

    prompt_tokens = sum(
        result["performance"].get("prompt_tokens") or 0
        for result in successful
    )

    completion_tokens = sum(
        result["performance"].get("completion_tokens") or 0
        for result in successful
    )

    quality_passes = sum(
        1
        for result in successful
        if result["quality"]["pass"]
    )

    quality_scores = [
        result["quality"]["score"]
        for result in successful
    ]

    return {
        "repetition": repetition,
        "concurrency": concurrency,
        "cache_state": cache_state,
        "warmup": warmup,
        "batch_elapsed_seconds": round(
            batch_elapsed,
            4,
        ),
        "aggregate": {
            "successful_requests": len(successful),
            "failed_requests": failed,
            "total_prompt_tokens": prompt_tokens,
            "total_completion_tokens": completion_tokens,
            "completion_tokens_per_second": round(
                completion_tokens / batch_elapsed,
                2,
            ) if batch_elapsed > 0 else None,
            "quality_pass_rate": round(
                quality_passes / len(successful),
                4,
            ) if successful else 0.0,
            "quality_score_mean": round(
                statistics.mean(quality_scores),
                4,
            ) if quality_scores else None,
        },
        "requests": results,
    }


def summarize_batches(
    batches: list[dict[str, Any]],
) -> dict[str, Any]:
    requests = [
        request
        for batch in batches
        for request in batch["requests"]
        if request.get("success")
    ]

    def perf(field: str) -> list[Any]:
        return [
            request["performance"].get(field)
            for request in requests
        ]

    quality_scores = [
        request["quality"]["score"]
        for request in requests
    ]

    quality_passes = sum(
        1
        for request in requests
        if request["quality"]["pass"]
    )

    batch_throughput = [
        batch["aggregate"]["completion_tokens_per_second"]
        for batch in batches
    ]

    return {
        "request_count": len(requests),
        "failed_request_count": sum(
            batch["aggregate"]["failed_requests"]
            for batch in batches
        ),
        "ttft_seconds": metric_summary(
            perf("ttft_seconds")
        ),
        "ttfa_seconds": metric_summary(
            perf("ttfa_seconds")
        ),
        "elapsed_seconds": metric_summary(
            perf("elapsed_seconds")
        ),
        "answer_tokens_per_second": metric_summary(
            perf("answer_tokens_per_second")
        ),
        "batch_completion_tokens_per_second": metric_summary(
            batch_throughput
        ),
        "quality": {
            "pass_rate": round(
                quality_passes / len(requests),
                4,
            ) if requests else 0.0,
            "score_mean": round(
                statistics.mean(quality_scores),
                4,
            ) if quality_scores else None,
            "score_min": round(
                min(quality_scores),
                4,
            ) if quality_scores else None,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Execute one experiment from an SRH experiment plan"
    )

    parser.add_argument("plan")
    parser.add_argument("experiment_id")
    parser.add_argument("pack")

    parser.add_argument(
        "--base-url",
        default="http://localhost:18300/v1",
    )

    parser.add_argument(
        "--model",
        default="qwen3.8-flash-next",
    )

    parser.add_argument(
        "--repetitions",
        type=int,
        default=3,
    )

    parser.add_argument(
        "--seed",
        type=int,
        default=42,
    )

    args = parser.parse_args()

    if args.repetitions < 1:
        parser.error("--repetitions must be >= 1")

    plan_path = Path(args.plan)
    pack_dir = Path(args.pack)

    plan = load_json(plan_path)
    experiment = find_experiment(
        plan,
        args.experiment_id,
    )

    manifest = load_json(
        pack_dir / "manifest.json"
    )

    ground_truth = load_json(
        pack_dir / "ground_truth.json"
    )

    base_request = (
        pack_dir / "request.txt"
    ).read_text(
        encoding="utf-8"
    )

    experiment_input = experiment["request"]["input_tokens"]
    pack_target = manifest["calibration"]["target_tokens"]

    if experiment_input != pack_target:
        raise SystemExit(
            "pack/experiment mismatch: "
            f"experiment expects {experiment_input} input tokens "
            f"but pack target is {pack_target}"
        )

    print("SRH EXPERIMENT EXECUTOR")
    print("=" * 72)
    print(f"Executor    : {EXECUTOR_VERSION}")
    print(f"Workload    : {plan['workload']['id']}")
    print(f"Experiment  : {args.experiment_id}")
    print(f"Concurrency : {experiment['concurrency']}")
    print(f"Cache state : {experiment['cache_state']}")
    print(f"Input target: {experiment_input}")
    print(
        f"Input actual: "
        f"{manifest['calibration']['actual_tokens']}"
    )
    print(f"Repetitions : {args.repetitions}")
    print()

    batches = []

    for repetition in range(
        1,
        args.repetitions + 1,
    ):
        print(
            f"repetition {repetition}/{args.repetitions}...",
            end=" ",
            flush=True,
        )

        batch = execute_batch(
            base_url=args.base_url,
            model=args.model,
            base_request=base_request,
            ground_truth=ground_truth,
            experiment=experiment,
            repetition=repetition,
            seed=args.seed,
        )

        batches.append(batch)

        agg = batch["aggregate"]

        print(
            f"batch={batch['batch_elapsed_seconds']}s | "
            f"throughput="
            f"{agg['completion_tokens_per_second']} tok/s | "
            f"quality={agg['quality_pass_rate']:.0%} | "
            f"errors={agg['failed_requests']}"
        )

    summary = summarize_batches(batches)

    report = {
        "schema": RESULT_SCHEMA,
        "executor_version": EXECUTOR_VERSION,
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "workload": plan["workload"],
        "experiment": experiment,
        "scenario": {
            "id": manifest["scenario_id"],
            "seed": manifest["seed"],
            "target_tokens": pack_target,
            "actual_tokens": (
                manifest["calibration"]["actual_tokens"]
            ),
        },
        "runtime": {
            "endpoint": args.base_url,
            "model": args.model,
        },
        "protocol": {
            "repetitions": args.repetitions,
            "arrival": "simultaneous",
            "percentile_method": "nearest-rank",
            "cache_semantics": {
                "cold": (
                    "no runtime prewarm; unique leading prefix "
                    "prevents measured prompt cache reuse"
                ),
                "warm": (
                    "runtime prewarmed with unrelated request; "
                    "measured prompts use unique leading prefix"
                ),
                "shared-prefix": (
                    "instructions and context are prewarmed; "
                    "measured requests preserve that prefix "
                    "and use distinct request tails"
                ),
            },
        },
        "summary": summary,
        "batches": batches,
    }

    root = Path(__file__).resolve().parent.parent.parent

    output_dir = (
        root
        / "srh"
        / "experiments"
        / "results"
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    timestamp = datetime.now().strftime(
        "%Y%m%d-%H%M%S"
    )

    output = (
        output_dir
        / f"{args.experiment_id}-{timestamp}.json"
    )

    output.write_text(
        json.dumps(
            report,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print()
    print("SUMMARY")
    print("-" * 72)

    for metric in (
        "ttft_seconds",
        "ttfa_seconds",
        "elapsed_seconds",
        "answer_tokens_per_second",
        "batch_completion_tokens_per_second",
    ):
        values = summary.get(metric)

        if values:
            print(
                f"{metric:<38} "
                f"median={values['median']:<8} "
                f"p95={values['p95']:<8} "
                f"p99={values['p99']:<8}"
            )

    print(
        f"{'quality_pass_rate':<38} "
        f"{summary['quality']['pass_rate']:.0%}"
    )

    print(
        f"{'quality_score_mean':<38} "
        f"{summary['quality']['score_mean']}"
    )

    print()
    print(f"Evidence written: {output}")


if __name__ == "__main__":
    main()
