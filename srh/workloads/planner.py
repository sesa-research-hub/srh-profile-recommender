#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
#
# Copyright 2026 Sesa Research Hub
#
# SRH Private AI - Workload Experiment Planner

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from contract import load_contract


PLANNER_VERSION = "0.1.0"
PLAN_SCHEMA = "srh.experiment-plan.v1"


def _point(
    *,
    experiment_id: str,
    purpose: str,
    concurrency: int,
    input_tokens: int,
    output_tokens: int,
    percentile_class: str,
    cache_state: str,
) -> dict[str, Any]:
    return {
        "experiment_id": experiment_id,
        "purpose": purpose,
        "concurrency": concurrency,
        "request": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "percentile_class": percentile_class,
        },
        "cache_state": cache_state,
    }


def build_plan(contract: dict[str, Any]) -> dict[str, Any]:
    workload_id = contract["id"]

    traffic = contract["traffic"]
    request = contract["request_profile"]
    context = contract.get("context_behavior", {})

    concurrency = sorted(set(traffic["concurrent_users"]))

    c_min = concurrency[0]
    c_max = concurrency[-1]

    input_dist = request["input_tokens"]
    output_dist = request["output_tokens"]

    in_p50 = input_dist["p50"]
    in_p95 = input_dist["p95"]
    in_max = input_dist.get("max", in_p95)

    out_p50 = output_dist["p50"]
    out_p95 = output_dist["p95"]
    out_max = output_dist.get("max", out_p95)

    repeated_probability = context.get(
        "repeated_context_probability",
        0.0,
    )

    shared_prefix_probability = context.get(
        "shared_prefix_probability",
        0.0,
    )

    experiments: list[dict[str, Any]] = []

    #
    # 1. Single-user baseline.
    #
    experiments.append(
        _point(
            experiment_id="baseline-p50-c1",
            purpose=(
                "Measure nominal single-user interactive latency "
                "and generation throughput."
            ),
            concurrency=c_min,
            input_tokens=in_p50,
            output_tokens=out_p50,
            percentile_class="p50",
            cache_state="warm",
        )
    )

    #
    # 2. Concurrency scaling at nominal request size.
    #
    for c in concurrency:
        if c == c_min:
            continue

        experiments.append(
            _point(
                experiment_id=f"concurrency-p50-c{c}",
                purpose=(
                    "Measure degradation and throughput scaling "
                    "under concurrent nominal requests."
                ),
                concurrency=c,
                input_tokens=in_p50,
                output_tokens=out_p50,
                percentile_class="p50",
                cache_state="warm",
            )
        )

    #
    # 3. p95 request envelope.
    #
    experiments.append(
        _point(
            experiment_id="tail-p95-c1",
            purpose=(
                "Measure latency for a representative tail request "
                "without concurrency interference."
            ),
            concurrency=c_min,
            input_tokens=in_p95,
            output_tokens=out_p95,
            percentile_class="p95",
            cache_state="warm",
        )
    )

    if c_max != c_min:
        experiments.append(
            _point(
                experiment_id=f"tail-p95-c{c_max}",
                purpose=(
                    "Measure interaction between tail-size requests "
                    "and maximum declared concurrency."
                ),
                concurrency=c_max,
                input_tokens=in_p95,
                output_tokens=out_p95,
                percentile_class="p95",
                cache_state="warm",
            )
        )

    #
    # 4. Maximum declared request envelope.
    #
    if in_max != in_p95 or out_max != out_p95:
        experiments.append(
            _point(
                experiment_id="max-envelope-c1",
                purpose=(
                    "Validate the maximum request envelope declared "
                    "by the workload contract."
                ),
                concurrency=c_min,
                input_tokens=in_max,
                output_tokens=out_max,
                percentile_class="max",
                cache_state="warm",
            )
        )

    #
    # 5. Cold-context probe.
    #
    experiments.append(
        _point(
            experiment_id="cold-p95-c1",
            purpose=(
                "Measure p95 request behavior without assuming "
                "prefix-cache reuse."
            ),
            concurrency=c_min,
            input_tokens=in_p95,
            output_tokens=out_p95,
            percentile_class="p95",
            cache_state="cold",
        )
    )

    #
    # 6. Shared/repeated-context probe only when the workload says
    #    cache reuse is materially present.
    #
    if repeated_probability > 0 or shared_prefix_probability > 0:
        experiments.append(
            _point(
                experiment_id=f"shared-prefix-p95-c{c_max}",
                purpose=(
                    "Quantify the benefit and scheduling behavior "
                    "of repeated or shared context."
                ),
                concurrency=c_max,
                input_tokens=in_p95,
                output_tokens=out_p50,
                percentile_class="p95",
                cache_state="shared-prefix",
            )
        )

    #
    # Remove accidental duplicates while preserving order.
    #
    unique: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()

    for experiment in experiments:
        request_spec = experiment["request"]

        key = (
            experiment["concurrency"],
            request_spec["input_tokens"],
            request_spec["output_tokens"],
            experiment["cache_state"],
        )

        if key in seen:
            continue

        seen.add(key)
        unique.append(experiment)

    return {
        "schema": PLAN_SCHEMA,
        "planner_version": PLANNER_VERSION,
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "workload": {
            "id": workload_id,
            "class": contract["workload_class"],
        },
        "source_contract_schema": contract["schema"],
        "service_objectives": contract["service_objectives"],
        "quality_requirements": contract.get("quality", {}),
        "execution_policy": {
            "reasoning": contract["request_profile"].get(
                "reasoning",
                "disabled",
            ),
            "streaming": contract["request_profile"].get(
                "streaming",
                True,
            ),
            "response_contract": contract["response_contract"],
        },
        "planning": {
            "strategy": "representative-envelope",
            "declared_concurrency": concurrency,
            "experiment_count": len(unique),
        },
        "experiments": unique,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate an SRH experiment plan from a workload contract"
    )

    parser.add_argument(
        "contract",
        help="Path to SRH Workload Contract JSON",
    )

    parser.add_argument(
        "--output",
        help="Optional output plan JSON path",
    )

    args = parser.parse_args()

    contract = load_contract(args.contract)
    plan = build_plan(contract)

    if args.output:
        output = Path(args.output)
    else:
        root = Path(__file__).resolve().parent
        output_dir = root / "plans"
        output_dir.mkdir(parents=True, exist_ok=True)

        output = (
            output_dir
            / f"{contract['id']}.plan.json"
        )

    output.parent.mkdir(parents=True, exist_ok=True)

    output.write_text(
        json.dumps(
            plan,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print("SRH EXPERIMENT PLANNER")
    print("=" * 60)
    print(f"Planner   : {PLANNER_VERSION}")
    print(f"Workload  : {contract['id']}")
    print(f"Strategy  : {plan['planning']['strategy']}")
    print(f"Experiments: {plan['planning']['experiment_count']}")
    print()

    for experiment in plan["experiments"]:
        req = experiment["request"]

        print(
            f"{experiment['experiment_id']:<28} "
            f"c={experiment['concurrency']:<2} "
            f"in={req['input_tokens']:<6} "
            f"out={req['output_tokens']:<5} "
            f"cache={experiment['cache_state']}"
        )

    print()
    print(f"Plan written: {output}")


if __name__ == "__main__":
    main()
