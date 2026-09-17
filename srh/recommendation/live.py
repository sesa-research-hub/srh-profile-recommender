#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Sesa Research Hub
"""Run and rank a guided, comparable campaign against local model runtimes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from srh.capacity.local_runtime import discover_local_models, run_local_benchmark
from srh.profiles.capture import canonical_sha256
from srh.workloads.contract import validate_contract


SCHEMA = "srh.live-deployment-comparison.v1"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _profile_id(runtime: dict[str, Any]) -> tuple[str, str]:
    identity = {
        "provider": runtime.get("provider"),
        "endpoint": runtime.get("endpoint"),
        "model": runtime.get("id"),
        "runtime_fingerprint": runtime.get("runtime_fingerprint"),
    }
    digest = canonical_sha256(identity)
    return f"srh-{digest[:12]}", digest


def _candidate_row(runtime: dict[str, Any], metadata: dict[str, Any], observation: dict[str, Any]) -> dict[str, Any]:
    mapping = {
        "ttfa_p95_ms": ("ttfa_p95", 1000),
        "end_to_end_p95_ms": ("end_to_end_p95", 1000),
        "answer_tokens_per_second_min": ("answer_speed_min", 1),
        "error_rate_max": ("error_rate", 1),
        "quality_minimum_score": ("quality_minimum", 1),
    }
    checks: dict[str, dict[str, Any]] = {}
    blocking: list[str] = []
    for output_name, (input_name, multiplier) in mapping.items():
        source = observation["checks"][input_name]
        actual = source["actual"] * multiplier if source["actual"] is not None else None
        target = source["target"] * multiplier
        checks[output_name] = {"actual": actual, "target": target, "pass": source["pass"]}
        if not source["pass"]:
            blocking.append(output_name)
    quality_checks_pass = observation["summary"]["quality_pass_rate"] == 1
    checks["required_quality_checks"] = {
        "actual": observation["summary"]["quality_pass_rate"], "target": 1, "pass": quality_checks_pass,
    }
    if not quality_checks_pass:
        blocking.append("required_quality_checks")
    profile_id, profile_sha256 = _profile_id(runtime)
    return {
        "profile_id": profile_id,
        "profile_sha256": profile_sha256,
        "source_evidence": "live local benchmark",
        "evidence_sha256": canonical_sha256(observation),
        "deployment": {
            "label": metadata.get("label") or runtime["label"],
            "hardware": {"label": "host locale"},
            "model": {
                "snapshot": runtime["id"], "served_names": [runtime["id"]],
                "digest": runtime.get("digest"), "family": runtime.get("family"),
                "parameter_size": runtime.get("parameter_size"), "quantization": runtime.get("quantization"),
            },
            "engine": runtime.get("provider"),
            "image_id": runtime.get("root"),
            "license_review_status": "outside_performance_comparison",
            "estimated_three_year_cost_eur": None,
            "cost_provenance": None,
            "device_power_w": None,
            "runtime_configuration": observation.get("runtime_configuration"),
            "energy_observation": observation.get("energy_observation"),
        },
        "checks": checks,
        "eligible": not blocking,
        "blocking_objectives": blocking,
        "observation": observation,
    }


def run_live_comparison(
    *, contract: dict[str, Any], candidates: list[dict[str, Any]], profile: str,
    repetitions: int, concurrency: int, policy: str,
    discovery: Callable[[], list[dict[str, Any]]] = discover_local_models,
    benchmark: Callable[..., dict[str, Any]] = run_local_benchmark,
) -> dict[str, Any]:
    errors, _ = validate_contract(contract)
    _require(not errors, "invalid workload contract: " + "; ".join(errors))
    _require(isinstance(candidates, list) and 2 <= len(candidates) <= 6, "select between two and six local candidates")
    _require(profile in {"quick", "representative", "stress"}, "unsupported comparison profile")
    _require(repetitions in {3, 5}, "comparison repetitions must be 3 or 5")
    _require(type(concurrency) is int and 1 <= concurrency <= 8, "concurrency must be between 1 and 8")
    _require(policy == "performance_first", "live local comparison supports performance_first only")

    inventory = {item["runtime_fingerprint"]: item for item in discovery()}
    fingerprints = [item.get("runtime_fingerprint") for item in candidates]
    _require(all(isinstance(value, str) and value for value in fingerprints), "runtime identity is required")
    _require(len(set(fingerprints)) == len(fingerprints), "select distinct runtime identities")

    rows: list[dict[str, Any]] = []
    for selected in candidates:  # Sequential by design: local models share the same accelerator.
        runtime = inventory.get(selected["runtime_fingerprint"])
        _require(runtime is not None, f"local runtime is no longer available: {selected.get('label', 'unknown')}")
        metadata = {"label": runtime["label"]}
        observation = benchmark(
            endpoint=runtime["endpoint"], model=runtime["id"], contract=contract,
            profile=profile, repetitions=repetitions, concurrency=concurrency,
        )
        rows.append(_candidate_row(runtime, metadata, observation))

    eligible = [row for row in rows if row["eligible"]]
    ranked = sorted(eligible, key=lambda row: (
        row["checks"]["ttfa_p95_ms"]["actual"], row["checks"]["end_to_end_p95_ms"]["actual"],
        -row["checks"]["answer_tokens_per_second_min"]["actual"], row["profile_sha256"],
    ))
    diagnostic_ranked = sorted(rows, key=lambda row: (
        -sum(1 for check in row["checks"].values() if check["pass"]),
        -(row["checks"]["quality_minimum_score"]["actual"] or 0),
        row["checks"]["ttfa_p95_ms"]["actual"] if row["checks"]["ttfa_p95_ms"]["actual"] is not None else float("inf"),
        row["checks"]["end_to_end_p95_ms"]["actual"] if row["checks"]["end_to_end_p95_ms"]["actual"] is not None else float("inf"),
        row["profile_sha256"],
    ))
    evidence_complete = repetitions == 5
    verdict = (
        "INSUFFICIENT_EVIDENCE" if not evidence_complete else
        "RECOMMENDED" if ranked else
        "NO_DEPLOYMENT_MEETS_REQUIREMENTS"
    )
    selected = ranked[0] if verdict == "RECOMMENDED" else None
    return {
        "schema": SCHEMA,
        "engine_version": "0.2.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "workload_id": contract["id"],
        "contract_sha256": canonical_sha256(contract),
        "verdict": verdict,
        "recommended_profile_id": selected["profile_id"] if selected else None,
        "recommended_label": selected["deployment"]["label"] if selected else None,
        "closest_candidate_profile_id": diagnostic_ranked[0]["profile_id"] if diagnostic_ranked else None,
        "closest_candidate_label": diagnostic_ranked[0]["deployment"]["label"] if diagnostic_ranked else None,
        "policy": policy,
        "policy_explanation": "Qualità e SLO sono vincoli obbligatori; tra i candidati idonei prevalgono TTFA p95, E2E p95 e throughput minimo. Costi e licenze non influenzano questo confronto prestazionale locale.",
        "decision_scope": "LAB_SYNTHETIC_SCENARIO",
        "comparison_scope": "stesso contratto, scenario, ground truth, evaluator, profilo, ripetizioni e concorrenza; modelli e runtime possono differire",
        "minimum_observed_deployments": 2,
        "comparable": True,
        "incompatible_fields": [],
        "protocol": {"profile": profile, "repetitions": repetitions, "concurrency": concurrency, "sequential_execution": True},
        "candidates": rows,
        "rejected_evidence": [],
        "ranking": [row["profile_id"] for row in ranked] if evidence_complete else [],
        "recommended_next_action": (
            "Eseguire il test di accettazione con documenti e ground truth del cliente." if verdict == "RECOMMENDED" else
            "Ripetere con cinque misure usando lo stesso protocollo." if verdict == "INSUFFICIENT_EVIDENCE" else
            "Provare una diversa configurazione o rivedere gli obiettivi con il cliente."
        ),
        "limitations": [
            "La recommendation riguarda lo scenario sintetico comune eseguito sul sistema locale.",
            "La qualità sul dominio cliente richiede documenti, ground truth e revisione di un esperto del cliente.",
            "Licenze, acquisto hardware e TCO sono valutazioni separate dal confronto prestazionale locale.",
            "Prima dell'impegno di produzione resta necessario un test di accettazione sul sito cliente.",
        ],
    }
