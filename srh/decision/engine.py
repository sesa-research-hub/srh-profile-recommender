#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
#
# Copyright 2026 Sesa Research Hub
#
# SRH Private AI - Evidence-driven Decision Engine

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


VERSION = "0.1.0"
SCHEMA = "srh.workload-decision.v1"


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))

    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")

    return data


def check(
    assessment: dict[str, Any],
    name: str,
) -> dict[str, Any] | None:
    return assessment.get("checks", {}).get(name)


def passed(
    assessment: dict[str, Any],
    name: str,
) -> bool | None:
    item = check(assessment, name)

    if item is None:
        return None

    return bool(item.get("pass"))


def failed_checks(
    assessment: dict[str, Any],
) -> list[dict[str, Any]]:
    failures = []

    for name, item in assessment.get("checks", {}).items():
        if not item.get("pass"):
            failures.append({
                "objective": name,
                "actual": item.get("actual"),
                "target": item.get("target"),
            })

    return failures


def confidence(
    evidence: dict[str, Any],
) -> dict[str, Any]:
    repetitions = evidence.get(
        "protocol",
        {},
    ).get(
        "repetitions",
        0,
    )

    if repetitions >= 20:
        level = "high"
    elif repetitions >= 5:
        level = "medium"
    else:
        level = "low"

    return {
        "level": level,
        "repetitions": repetitions,
        "note": (
            "Confidence reflects experimental repetition count only; "
            "it does not yet account for cross-day or cross-runtime variance."
        ),
    }


def diagnose(
    evidence: dict[str, Any],
) -> dict[str, Any]:
    slo = evidence["slo_assessment"]

    probe = slo["probe"]
    assistant = slo["assistant"]

    summaries = evidence["summaries"]

    probe_ttfa = (
        summaries["probe"]["ttfa_seconds"]["median"]
    )

    assistant_ttfa = (
        summaries["assistant"]["ttfa_seconds"]["median"]
    )

    ttfa_ratio = (
        assistant_ttfa / probe_ttfa
        if probe_ttfa > 0
        else None
    )

    quality_ok = (
        passed(probe, "quality_minimum_score") is not False
        and passed(assistant, "quality_minimum_score") is not False
    )

    reliability_ok = (
        passed(probe, "error_rate_max") is not False
        and passed(assistant, "error_rate_max") is not False
    )

    decode_ok = (
        passed(
            probe,
            "answer_tokens_per_second_min",
        ) is not False
        and passed(
            assistant,
            "answer_tokens_per_second_min",
        ) is not False
    )

    probe_ttfa_ok = passed(
        probe,
        "ttfa_p95_ms",
    )

    assistant_ttfa_ok = passed(
        assistant,
        "ttfa_p95_ms",
    )

    probe_e2e_ok = passed(
        probe,
        "end_to_end_p95_ms",
    )

    assistant_e2e_ok = passed(
        assistant,
        "end_to_end_p95_ms",
    )

    signals: list[str] = []

    if not quality_ok:
        category = "QUALITY_BOUND"
        signals.append(
            "one or both response modes fail the quality requirement"
        )

    elif not reliability_ok:
        category = "RELIABILITY_BOUND"
        signals.append(
            "one or both response modes exceed the allowed error rate"
        )

    elif (
        probe_ttfa_ok is False
        and assistant_ttfa_ok is False
        and decode_ok
    ):
        category = "CONTEXT_PREFILL_BOUND"

        signals.append(
            "TTFA fails in both probe and assistant modes"
        )

        signals.append(
            "decode throughput remains above its service objective"
        )

        if (
            ttfa_ratio is not None
            and 0.8 <= ttfa_ratio <= 1.2
        ):
            signals.append(
                "probe and assistant median TTFA are within 20%, "
                "indicating response verbosity is not driving TTFA"
            )

    elif (
        probe_ttfa_ok is not False
        and assistant_ttfa_ok is not False
        and probe_e2e_ok is not False
        and assistant_e2e_ok is False
    ):
        category = "RESPONSE_GENERATION_BOUND"

        signals.append(
            "TTFA passes but assistant end-to-end latency fails"
        )

        signals.append(
            "probe end-to-end latency remains within objective"
        )

    elif not decode_ok:
        category = "DECODE_BOUND"

        signals.append(
            "answer generation throughput falls below its service objective"
        )

    else:
        category = "UNRESOLVED"

        signals.append(
            "current evidence does not uniquely identify a bottleneck"
        )

    return {
        "category": category,
        "signals": signals,
        "probe_assistant_ttfa_ratio": (
            round(ttfa_ratio, 4)
            if ttfa_ratio is not None
            else None
        ),
    }


def next_experiment(
    evidence: dict[str, Any],
    diagnosis: dict[str, Any],
) -> dict[str, Any]:
    experiment = evidence["experiment"]

    category = diagnosis["category"]

    if category == "CONTEXT_PREFILL_BOUND":
        return {
            "action": "RUN_SHARED_PREFIX_CONTROL",
            "reason": (
                "Determine how much of the failing TTFA is recoverable "
                "through application-level context reuse."
            ),
            "experiment_spec": {
                "concurrency": experiment["concurrency"],
                "input_tokens": experiment["request"]["input_tokens"],
                "output_tokens": experiment["request"]["output_tokens"],
                "cache_state": "shared-prefix",
                "response_modes": [
                    "probe",
                    "assistant",
                ],
            },
            "decision_question": (
                "Can prefix reuse bring TTFA within the workload SLO "
                "without reducing quality?"
            ),
        }

    if category == "RESPONSE_GENERATION_BOUND":
        return {
            "action": "TEST_RESPONSE_POLICY",
            "reason": (
                "The latency blocker occurs after first-token delivery."
            ),
            "experiment_spec": {
                "concurrency": experiment["concurrency"],
                "input_tokens": experiment["request"]["input_tokens"],
                "cache_state": experiment["cache_state"],
                "compare": [
                    "probe",
                    "assistant",
                ],
            },
            "decision_question": (
                "Can a tighter response policy meet E2E latency "
                "without compromising quality?"
            ),
        }

    if category == "DECODE_BOUND":
        return {
            "action": "TEST_RUNTIME_PROFILE",
            "reason": (
                "Generation throughput, rather than context ingestion, "
                "is below the workload objective."
            ),
            "decision_question": (
                "Which runtime profile improves decode throughput while "
                "preserving quality?"
            ),
        }

    if category == "QUALITY_BOUND":
        return {
            "action": "TEST_MODEL_OR_REASONING_POLICY",
            "reason": (
                "Performance tuning is not useful until correctness "
                "satisfies the workload quality gate."
            ),
            "decision_question": (
                "Does another model or reasoning policy satisfy the "
                "quality contract?"
            ),
        }

    if category == "RELIABILITY_BOUND":
        return {
            "action": "INVESTIGATE_RUNTIME_STABILITY",
            "reason": (
                "Request failures exceed the service objective."
            ),
            "decision_question": (
                "Are failures caused by saturation, runtime errors, "
                "or workload-specific behavior?"
            ),
        }

    return {
        "action": "COLLECT_DISCRIMINATING_EVIDENCE",
        "reason": (
            "Current evidence is insufficient for a unique diagnosis."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Convert SRH experimental evidence into an operational decision"
        )
    )

    parser.add_argument(
        "evidence",
        help="Paired experiment evidence JSON",
    )

    args = parser.parse_args()

    source = Path(args.evidence)
    evidence = load_json(source)

    if evidence.get("schema") != "srh.paired-response-experiment.v1":
        raise SystemExit(
            "unsupported evidence schema: "
            f"{evidence.get('schema')!r}"
        )

    assistant_assessment = evidence[
        "slo_assessment"
    ]["assistant"]

    blockers = failed_checks(
        assistant_assessment
    )

    if assistant_assessment.get("pass"):
        verdict = "CURRENT_PROFILE_MEETS_SLO"
    else:
        verdict = "CURRENT_PROFILE_FAILS_SLO"

    diagnosis = diagnose(evidence)
    recommendation = next_experiment(
        evidence,
        diagnosis,
    )

    decision = {
        "schema": SCHEMA,
        "engine_version": VERSION,
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),

        "source_evidence": str(source),

        "workload": evidence["workload"],
        "scenario": evidence["scenario"],
        "runtime": evidence["runtime"],

        "verdict": verdict,

        "blocking_objectives": blockers,

        "diagnosis": diagnosis,

        "confidence": confidence(evidence),

        "recommended_next_experiment": recommendation,

        "limitations": [
            (
                "The evidence identifies the model and endpoint but "
                "does not yet contain a complete immutable runtime-profile "
                "fingerprint (MODE, GPU_MEM, CTX, SEQS, MTP and related "
                "runtime parameters)."
            ),
            (
                "The verdict applies only to the currently tested "
                "configuration and workload envelope; it is not evidence "
                "that no alternative profile can meet the SLO."
            ),
        ],
    }

    root = Path(__file__).resolve().parent.parent.parent

    output_dir = (
        root
        / "srh"
        / "decision"
        / "results"
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    timestamp = datetime.now().strftime(
        "%Y%m%d-%H%M%S"
    )

    experiment_id = evidence[
        "experiment"
    ]["experiment_id"]

    output = (
        output_dir
        / f"decision-{experiment_id}-{timestamp}.json"
    )

    output.write_text(
        json.dumps(
            decision,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print("SRH WORKLOAD DECISION")
    print("=" * 72)

    print(
        f"Workload   : {evidence['workload']['id']}"
    )

    print(
        f"Experiment : {experiment_id}"
    )

    print(
        f"Verdict    : {verdict}"
    )

    print(
        f"Diagnosis  : {diagnosis['category']}"
    )

    print(
        f"Confidence : "
        f"{decision['confidence']['level']}"
    )

    print()

    print("BLOCKING OBJECTIVES")
    print("-" * 72)

    if not blockers:
        print("None")
    else:
        for blocker in blockers:
            print(
                f"{blocker['objective']:<32} "
                f"actual={blocker['actual']} "
                f"target={blocker['target']}"
            )

    print()
    print("DIAGNOSTIC SIGNALS")
    print("-" * 72)

    for signal in diagnosis["signals"]:
        print(f"- {signal}")

    print()
    print("RECOMMENDED NEXT EXPERIMENT")
    print("-" * 72)

    print(
        recommendation["action"]
    )

    print(
        recommendation["reason"]
    )

    if recommendation.get("decision_question"):
        print()
        print(
            "Decision question: "
            f"{recommendation['decision_question']}"
        )

    print()
    print(f"Decision written: {output}")


if __name__ == "__main__":
    main()
