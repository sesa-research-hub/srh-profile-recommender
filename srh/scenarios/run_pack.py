#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
#
# Copyright 2026 Sesa Research Hub
#
# SRH Private AI - Scenario Pack Executor

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from srh.benchmark import stream_chat


RUNNER_VERSION = "0.1.0"
RESULT_SCHEMA = "srh.scenario-evaluation.v1"


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(
        char
        for char in text
        if not unicodedata.combining(char)
    )
    return " ".join(text.lower().split())


def contains_identifier(
    text: str,
    identifier: str,
) -> bool:
    return bool(
        re.search(
            rf"(?<![A-Za-z0-9]){re.escape(identifier)}(?![A-Za-z0-9])",
            text,
            flags=re.IGNORECASE,
        )
    )


def extract_structured_result(
    answer: str,
    marker: str,
) -> dict[str, Any] | None:
    for line in reversed(answer.splitlines()):
        stripped = line.strip()

        if not stripped.startswith(marker):
            continue

        raw = stripped[len(marker):].strip()

        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return None

        if isinstance(value, dict):
            return value

        return None

    return None


def evaluate_answer(
    answer: str,
    ground_truth: dict[str, Any],
) -> dict[str, Any]:
    expected = ground_truth["expected"]
    evaluation = ground_truth.get("evaluation", {})

    marker = evaluation.get(
        "marker",
        "SRH_RESULT:",
    )

    selected = extract_structured_result(
        answer,
        marker,
    )

    structured_ok = selected is not None

    if selected is None:
        selected = {}

    order_ok = (
        normalize(str(selected.get("order", "")))
        == normalize(expected["order"])
    )

    activity_ok = (
        normalize(str(selected.get("activity", "")))
        == normalize(expected["activity"])
    )

    evidence_ok = (
        normalize(
            str(selected.get("evidence_source_id", ""))
        )
        == normalize(expected["evidence_source_id"])
    )

    selected_values = {
        normalize(str(value))
        for value in selected.values()
    }

    forbidden_selected = [
        value
        for value in ground_truth.get(
            "forbidden_selections",
            [],
        )
        if normalize(value) in selected_values
    ]

    checks = {
        "structured_result": structured_ok,
        "expected_order": order_ok,
        "expected_activity": activity_ok,
        "expected_evidence": evidence_ok,
        "no_forbidden_selection": not forbidden_selected,
    }

    passed = sum(checks.values())
    total = len(checks)
    score = passed / total if total else 0.0

    return {
        "checks": checks,
        "passed_checks": passed,
        "total_checks": total,
        "score": round(score, 4),
        "selected_result": selected,
        "forbidden_selections_found": forbidden_selected,
        "pass": score == 1.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Execute and evaluate an SRH Synthetic Scenario Pack"
        )
    )

    parser.add_argument(
        "pack",
        help="Path to generated scenario pack directory",
    )

    parser.add_argument(
        "--base-url",
        default="http://localhost:18300/v1",
    )

    parser.add_argument(
        "--model",
        default="qwen3.8-flash-next",
    )

    parser.add_argument(
        "--max-tokens",
        type=int,
        default=512,
    )

    parser.add_argument(
        "--seed",
        type=int,
        default=42,
    )

    args = parser.parse_args()

    pack_dir = Path(args.pack)

    request_path = pack_dir / "request.txt"
    truth_path = pack_dir / "ground_truth.json"
    manifest_path = pack_dir / "manifest.json"

    request_text = request_path.read_text(
        encoding="utf-8",
    )

    ground_truth = json.loads(
        truth_path.read_text(
            encoding="utf-8",
        )
    )

    manifest = json.loads(
        manifest_path.read_text(
            encoding="utf-8",
        )
    )

    config = {
        "mode": "no-think",
        "messages": [
            {
                "role": "user",
                "content": request_text,
            }
        ],
        "max_tokens": args.max_tokens,
        "sampling": {
            "temperature": 0.0,
        },
    }

    print("SRH SCENARIO EVALUATION")
    print("=" * 68)
    print(f"Runner      : {RUNNER_VERSION}")
    print(f"Scenario    : {manifest['scenario_id']}")
    print(
        "Input target: "
        f"{manifest['calibration']['target_tokens']}"
    )
    print(
        "Input actual: "
        f"{manifest['calibration']['actual_tokens']}"
    )
    print(f"Model       : {args.model}")
    print()

    result = stream_chat(
        args.base_url,
        args.model,
        config,
        args.seed,
    )

    quality = evaluate_answer(
        result["answer"],
        ground_truth,
    )

    evidence = {
        "schema": RESULT_SCHEMA,
        "runner_version": RUNNER_VERSION,
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "scenario": {
            "id": manifest["scenario_id"],
            "seed": manifest["seed"],
            "target_tokens": (
                manifest["calibration"]["target_tokens"]
            ),
            "calibrated_tokens": (
                manifest["calibration"]["actual_tokens"]
            ),
            "target_position_ratio": (
                manifest["corpus"]["target_position_ratio"]
            ),
        },
        "runtime": {
            "endpoint": args.base_url,
            "model": args.model,
            "max_tokens": args.max_tokens,
            "seed": args.seed,
        },
        "performance": {
            key: value
            for key, value in result.items()
            if key != "answer"
        },
        "quality": quality,
        "answer": result["answer"],
    }

    root = Path(__file__).resolve().parent.parent.parent

    output_dir = (
        root
        / "srh"
        / "scenarios"
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
            f"{manifest['scenario_id']}-"
            f"{manifest['calibration']['target_tokens']}-"
            f"{timestamp}.json"
        )
    )

    output.write_text(
        json.dumps(
            evidence,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print("PERFORMANCE")
    print("-" * 68)
    print(
        f"prompt_tokens       : {result['prompt_tokens']}"
    )
    print(
        f"completion_tokens   : {result['completion_tokens']}"
    )
    print(
        f"TTFT                : {result['ttft_seconds']} s"
    )
    print(
        f"TTFA                : {result['ttfa_seconds']} s"
    )
    print(
        "answer tok/s        : "
        f"{result['answer_tokens_per_second']}"
    )
    print(
        f"end-to-end          : {result['elapsed_seconds']} s"
    )
    print()

    print("QUALITY")
    print("-" * 68)

    for name, passed in quality["checks"].items():
        print(
            f"{name:<26}: "
            f"{'PASS' if passed else 'FAIL'}"
        )

    print(
        f"{'quality_score':<26}: "
        f"{quality['score']:.2f}"
    )

    print(
        f"{'overall':<26}: "
        f"{'PASS' if quality['pass'] else 'FAIL'}"
    )

    print()
    print("ANSWER")
    print("-" * 68)
    print(result["answer"])
    print()
    print(f"Evidence written: {output}")


if __name__ == "__main__":
    main()
