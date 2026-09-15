#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
#
# Copyright 2026 Sesa Research Hub
#
# SRH Private AI - Paired Response Mode Experiment

from __future__ import annotations

import argparse
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from srh.experiments.executor import (
    execute_batch,
    find_experiment,
    load_json,
    resolve_reasoning_mode,
    summarize_batches,
)


VERSION = "0.1.0"
SCHEMA = "srh.paired-response-experiment.v1"


def generation_limit(
    response_contract: dict[str, Any],
    mode: str,
) -> int:
    key = f"{mode}_max_tokens"
    value = response_contract.get(key)

    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 1
    ):
        raise ValueError(
            f"invalid or missing {key}"
        )

    return value


def slo_assessment(
    summary: dict[str, Any],
    service_objectives: dict[str, Any],
    quality_requirements: dict[str, Any],
) -> dict[str, Any]:

    checks: dict[str, Any] = {}

    ttfa_target = service_objectives.get(
        "ttfa_p95_ms"
    )

    if ttfa_target is not None:
        actual = (
            summary.get("ttfa_seconds") or {}
        ).get("p95")

        checks["ttfa_p95_ms"] = {
            "target": ttfa_target,
            "actual": (
                round(actual * 1000, 2)
                if actual is not None
                else None
            ),
            "pass": (
                actual is not None
                and actual * 1000 <= ttfa_target
            ),
        }

    answer_tps_target = service_objectives.get(
        "answer_tokens_per_second_min"
    )

    if answer_tps_target is not None:
        # Conservative interpretation:
        # every observed successful request must satisfy the floor.
        actual = (
            summary.get(
                "answer_tokens_per_second"
            ) or {}
        ).get("min")

        checks["answer_tokens_per_second_min"] = {
            "target": answer_tps_target,
            "actual": actual,
            "pass": (
                actual is not None
                and actual >= answer_tps_target
            ),
        }

    e2e_target = service_objectives.get(
        "end_to_end_p95_ms"
    )

    if e2e_target is not None:
        actual = (
            summary.get("elapsed_seconds") or {}
        ).get("p95")

        checks["end_to_end_p95_ms"] = {
            "target": e2e_target,
            "actual": (
                round(actual * 1000, 2)
                if actual is not None
                else None
            ),
            "pass": (
                actual is not None
                and actual * 1000 <= e2e_target
            ),
        }

    error_target = service_objectives.get(
        "error_rate_max"
    )

    if error_target is not None:
        successful = summary.get(
            "request_count",
            0,
        )

        failed = summary.get(
            "failed_request_count",
            0,
        )

        total = successful + failed

        actual = (
            failed / total
            if total
            else 1.0
        )

        checks["error_rate_max"] = {
            "target": error_target,
            "actual": round(actual, 4),
            "pass": actual <= error_target,
        }

    minimum_quality = quality_requirements.get(
        "minimum_score"
    )

    if minimum_quality is not None:
        actual = (
            summary.get("quality") or {}
        ).get("score_min")

        checks["quality_minimum_score"] = {
            "target": minimum_quality,
            "actual": actual,
            "pass": (
                actual is not None
                and actual >= minimum_quality
            ),
        }

    return {
        "checks": checks,
        "pass": all(
            item["pass"]
            for item in checks.values()
        ) if checks else True,
    }


def decomposition(
    probe: dict[str, Any],
    assistant: dict[str, Any],
) -> dict[str, Any]:

    probe_ttfa = probe["ttfa_seconds"]["median"]
    probe_e2e = probe["elapsed_seconds"]["median"]

    assistant_ttfa = (
        assistant["ttfa_seconds"]["median"]
    )

    assistant_e2e = (
        assistant["elapsed_seconds"]["median"]
    )

    return {
        "probe": {
            "time_to_first_answer_seconds": probe_ttfa,
            "post_first_token_seconds": round(
                probe_e2e - probe_ttfa,
                4,
            ),
            "end_to_end_seconds": probe_e2e,
        },

        "assistant": {
            "time_to_first_answer_seconds": assistant_ttfa,
            "post_first_token_seconds": round(
                assistant_e2e - assistant_ttfa,
                4,
            ),
            "end_to_end_seconds": assistant_e2e,
        },

        "comparison": {
            "ttfa_delta_seconds": round(
                assistant_ttfa - probe_ttfa,
                4,
            ),

            "assistant_e2e_overhead_vs_probe_seconds": round(
                assistant_e2e - probe_e2e,
                4,
            ),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run paired SRH probe/assistant experiments"
        )
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
        default=5,
    )

    parser.add_argument(
        "--seed",
        type=int,
        default=42,
    )

    parser.add_argument(
        "--reasoning-mode",
        choices=[
            "plan",
            "disabled",
            "required",
        ],
        default="plan",
    )

    args = parser.parse_args()

    if args.repetitions < 1:
        parser.error(
            "--repetitions must be >= 1"
        )

    plan = load_json(
        Path(args.plan)
    )

    experiment = find_experiment(
        plan,
        args.experiment_id,
    )

    pack_dir = Path(args.pack)

    manifest = load_json(
        pack_dir / "manifest.json"
    )

    ground_truth = load_json(
        pack_dir / "ground_truth.json"
    )

    request_text = (
        pack_dir / "request.txt"
    ).read_text(
        encoding="utf-8"
    )

    expected_tokens = (
        experiment["request"]["input_tokens"]
    )

    pack_tokens = (
        manifest["calibration"]["target_tokens"]
    )

    if expected_tokens != pack_tokens:
        raise SystemExit(
            "pack/experiment mismatch: "
            f"experiment={expected_tokens}, "
            f"pack={pack_tokens}"
        )

    execution_policy = plan.get(
        "execution_policy",
        {},
    )

    reasoning_policy = execution_policy.get(
        "reasoning",
        "disabled",
    )

    try:
        model_mode = resolve_reasoning_mode(
            reasoning_policy,
            args.reasoning_mode,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    response_contract = execution_policy.get(
        "response_contract",
        {},
    )

    allowed = response_contract.get(
        "modes",
        [],
    )

    for required in ("probe", "assistant"):
        if required not in allowed:
            raise SystemExit(
                f"workload does not allow "
                f"response mode {required!r}"
            )

    limits = {
        mode: generation_limit(
            response_contract,
            mode,
        )
        for mode in ("probe", "assistant")
    }

    run_ids = {
        "probe": uuid.uuid4().hex[:12],
        "assistant": uuid.uuid4().hex[:12],
    }

    batches: dict[str, list[dict[str, Any]]] = {
        "probe": [],
        "assistant": [],
    }

    print("SRH PAIRED RESPONSE EXPERIMENT")
    print("=" * 72)
    print(f"Version     : {VERSION}")
    print(f"Workload    : {plan['workload']['id']}")
    print(f"Experiment  : {args.experiment_id}")
    print(f"Concurrency : {experiment['concurrency']}")
    print(f"Cache state : {experiment['cache_state']}")
    print(
        f"Reasoning   : "
        f"{reasoning_policy} -> {model_mode}"
    )
    print(f"Input actual: {manifest['calibration']['actual_tokens']}")
    print(f"Repetitions : {args.repetitions}")
    print()

    for repetition in range(
        1,
        args.repetitions + 1,
    ):
        # Alternate order to reduce systematic runtime/thermal drift.
        order = (
            ["probe", "assistant"]
            if repetition % 2
            else ["assistant", "probe"]
        )

        print(
            f"pair {repetition}/{args.repetitions} "
            f"order={'+'.join(order)}"
        )

        for mode in order:
            batch = execute_batch(
                base_url=args.base_url,
                model=args.model,
                base_request=request_text,
                ground_truth=ground_truth,
                experiment=experiment,
                repetition=repetition,
                seed=args.seed,
                model_mode=model_mode,
                response_mode=mode,
                generation_max_tokens=limits[mode],
                run_id=run_ids[mode],
            )

            batches[mode].append(batch)

            agg = batch["aggregate"]

            print(
                f"  {mode:<9} "
                f"batch={batch['batch_elapsed_seconds']:<8} "
                f"effective={agg['completion_tokens_per_second']:<6} "
                f"quality={agg['quality_pass_rate']:.0%}"
            )

    summaries = {
        mode: summarize_batches(
            batches[mode]
        )
        for mode in ("probe", "assistant")
    }

    service_objectives = plan.get(
        "service_objectives",
        {},
    )

    quality_requirements = plan.get(
        "quality_requirements",
        {},
    )

    slo = {
        mode: slo_assessment(
            summaries[mode],
            service_objectives,
            quality_requirements,
        )
        for mode in ("probe", "assistant")
    }

    timing = decomposition(
        summaries["probe"],
        summaries["assistant"],
    )

    report = {
        "schema": SCHEMA,
        "version": VERSION,
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),

        "workload": plan["workload"],
        "experiment": experiment,

        "scenario": {
            "id": manifest["scenario_id"],
            "target_tokens": pack_tokens,
            "actual_tokens": (
                manifest["calibration"]["actual_tokens"]
            ),
        },

        "runtime": {
            "endpoint": args.base_url,
            "model": args.model,
        },

        "protocol": {
            "type": "paired-interleaved",
            "repetitions": args.repetitions,
            "reasoning_policy": reasoning_policy,
            "resolved_model_mode": model_mode,
            "run_ids": run_ids,
            "mode_order": (
                "alternating probe/assistant"
            ),
        },

        "summaries": summaries,
        "timing_decomposition": timing,
        "slo_assessment": slo,
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
        / (
            f"paired-{args.experiment_id}-"
            f"{timestamp}.json"
        )
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
    print("MODE SUMMARY")
    print("-" * 72)

    for mode in ("probe", "assistant"):
        summary = summaries[mode]

        print(
            f"{mode:<10} "
            f"TTFA median="
            f"{summary['ttfa_seconds']['median']}s | "
            f"p95={summary['ttfa_seconds']['p95']}s | "
            f"E2E median="
            f"{summary['elapsed_seconds']['median']}s | "
            f"quality="
            f"{summary['quality']['pass_rate']:.0%}"
        )

    print()
    print("TIMING DECOMPOSITION")
    print("-" * 72)

    print(
        "probe TTFA                : "
        f"{timing['probe']['time_to_first_answer_seconds']} s"
    )

    print(
        "probe post-first-token    : "
        f"{timing['probe']['post_first_token_seconds']} s"
    )

    print(
        "assistant TTFA            : "
        f"{timing['assistant']['time_to_first_answer_seconds']} s"
    )

    print(
        "assistant post-first-token: "
        f"{timing['assistant']['post_first_token_seconds']} s"
    )

    print(
        "assistant E2E overhead    : "
        f"{timing['comparison']['assistant_e2e_overhead_vs_probe_seconds']} s"
    )

    print()
    print("SLO ASSESSMENT")
    print("-" * 72)

    for mode in ("probe", "assistant"):
        assessment = slo[mode]

        print(
            f"{mode:<10}: "
            f"{'PASS' if assessment['pass'] else 'FAIL'}"
        )

        for name, check in assessment["checks"].items():
            print(
                f"  {name:<32} "
                f"actual={check['actual']!s:<10} "
                f"target={check['target']!s:<10} "
                f"{'PASS' if check['pass'] else 'FAIL'}"
            )

    print()
    print(f"Evidence written: {output}")


if __name__ == "__main__":
    main()
