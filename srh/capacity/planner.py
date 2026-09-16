#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Sesa Research Hub
"""Translate business inputs into an auditable private-AI capacity shortlist."""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .catalog import load_catalogs
from ..profiles.capture import canonical_sha256
from ..workloads.contract import validate_contract
from ..workloads.planner import build_plan


PLANNER_VERSION = "0.3.0"
INTAKE_SCHEMA = "srh.client-intake.v1"
OUTPUT_SCHEMA = "srh.capacity-plan.v1"
GIB = 1024 ** 3


class CapacityPlanningError(ValueError):
    """Raised when meeting inputs cannot produce a defensible plan."""


def _positive(value: Any, name: str, *, integer: bool = False) -> float:
    valid_type = isinstance(value, int if integer else (int, float))
    if not valid_type or isinstance(value, bool) or value <= 0:
        raise CapacityPlanningError(f"{name} must be a positive {'integer' if integer else 'number'}")
    return float(value)


def _distribution(value: Any, name: str) -> dict[str, float]:
    if not isinstance(value, dict):
        raise CapacityPlanningError(f"{name} must be an object")
    result = {key: _positive(value.get(key), f"{name}.{key}") for key in ("p50", "p95", "max")}
    if not result["p50"] <= result["p95"] <= result["max"]:
        raise CapacityPlanningError(f"{name} must satisfy p50 <= p95 <= max")
    return result


def validate_intake(intake: dict[str, Any]) -> None:
    if intake.get("schema") != INTAKE_SCHEMA:
        raise CapacityPlanningError(f"schema must be {INTAKE_SCHEMA}")
    if not isinstance(intake.get("id"), str) or not intake["id"].strip():
        raise CapacityPlanningError("id must be a non-empty string")
    traffic = intake.get("traffic")
    if not isinstance(traffic, dict):
        raise CapacityPlanningError("traffic must be an object")
    total = _positive(traffic.get("total_users"), "traffic.total_users", integer=True)
    concurrent = _positive(traffic.get("concurrent_users"), "traffic.concurrent_users", integer=True)
    if concurrent > total:
        raise CapacityPlanningError("concurrent users cannot exceed total users")
    _positive(traffic.get("requests_per_day"), "traffic.requests_per_day")
    _positive(traffic.get("peak_requests_per_minute"), "traffic.peak_requests_per_minute")
    documents = intake.get("documents")
    if not isinstance(documents, dict):
        raise CapacityPlanningError("documents must be an object")
    _distribution(documents.get("pages"), "documents.pages")
    _distribution(documents.get("retrieved_pages"), "documents.retrieved_pages")
    for name in ("scanned_fraction", "shared_prefix_probability", "repeated_context_probability"):
        value = documents.get(name, 0)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 1:
            raise CapacityPlanningError(f"documents.{name} must be between 0 and 1")
    responses = intake.get("responses")
    if not isinstance(responses, dict):
        raise CapacityPlanningError("responses must be an object")
    _distribution(responses.get("words"), "responses.words")
    experience = intake.get("experience")
    if not isinstance(experience, dict):
        raise CapacityPlanningError("experience must be an object")
    first = _positive(experience.get("first_useful_response_seconds"), "experience.first_useful_response_seconds")
    complete = _positive(experience.get("complete_response_seconds"), "experience.complete_response_seconds")
    if complete <= first:
        raise CapacityPlanningError("complete response target must exceed first useful response target")
    error_rate = experience.get("error_rate_max", 0.01)
    if not isinstance(error_rate, (int, float)) or isinstance(error_rate, bool) or not 0 <= error_rate <= 1:
        raise CapacityPlanningError("experience.error_rate_max must be between 0 and 1")
    quality = intake.get("quality", {})
    if not isinstance(quality, dict):
        raise CapacityPlanningError("quality must be an object")
    minimum_score = quality.get("minimum_score", 0.95)
    if not isinstance(minimum_score, (int, float)) or isinstance(minimum_score, bool) or not 0 <= minimum_score <= 1:
        raise CapacityPlanningError("quality.minimum_score must be between 0 and 1")
    exploration = intake.get("exploration")
    if not isinstance(exploration, dict):
        raise CapacityPlanningError("exploration must be an object")
    for name in ("hardware_ids", "model_ids", "weight_bits"):
        if not isinstance(exploration.get(name), list) or not exploration[name]:
            raise CapacityPlanningError(f"exploration.{name} must be a non-empty array")
    if any(not isinstance(bits, int) or isinstance(bits, bool) or bits <= 0 for bits in exploration["weight_bits"]):
        raise CapacityPlanningError("exploration.weight_bits must contain positive integers")
    requested = exploration.get("requested_model_families", [])
    if not isinstance(requested, list) or any(not isinstance(value, str) or not value.strip() for value in requested):
        raise CapacityPlanningError("exploration.requested_model_families must contain non-empty strings")


def translate_intake(intake: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Translate client-language inputs into a validated Workload Contract."""
    validate_intake(intake)
    docs = intake["documents"]
    responses = intake["responses"]
    traffic = intake["traffic"]
    experience = intake["experience"]
    quality = intake.get("quality", {})
    assumptions = intake.get("translation_assumptions", {})
    words_per_page = float(assumptions.get("words_per_page", 450))
    tokens_per_word = float(assumptions.get("tokens_per_word", 1.34))
    prompt_overhead = int(assumptions.get("prompt_overhead_tokens", 256))
    if words_per_page <= 0 or tokens_per_word <= 0 or prompt_overhead < 0:
        raise CapacityPlanningError("translation assumptions must be non-negative")
    context_mode = docs.get("context_mode", "retrieval")
    page_dist = docs["retrieved_pages"] if context_mode == "retrieval" else docs["pages"]
    input_tokens = {
        key: int(math.ceil(page_dist[key] * words_per_page * tokens_per_word + prompt_overhead))
        for key in ("p50", "p95", "max")
    }
    output_tokens = {
        key: int(math.ceil(responses["words"][key] * tokens_per_word))
        for key in ("p50", "p95", "max")
    }
    concurrency = int(traffic["concurrent_users"])
    complete_window = experience["complete_response_seconds"] - experience["first_useful_response_seconds"]
    minimum_answer_tps = max(1.0, output_tokens["p50"] / complete_window)
    contract = {
        "schema": "srh.workload-contract.v1",
        "id": intake["id"],
        "name": intake.get("name", intake["id"]),
        "description": intake.get("description", "Capacity-planning workload translated from a client discovery session."),
        "workload_class": intake.get("workload_class", "mixed"),
        "traffic": {
            "concurrent_users": [1] if concurrency == 1 else [1, concurrency],
            "requests_per_minute": float(traffic["peak_requests_per_minute"]),
            "burst_factor": float(traffic.get("burst_factor", 1.5)),
        },
        "request_profile": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "reasoning": intake.get("reasoning", "optional"),
            "streaming": True,
        },
        "context_behavior": {
            "shared_prefix_probability": float(docs.get("shared_prefix_probability", 0.3)),
            "repeated_context_probability": float(docs.get("repeated_context_probability", 0.2)),
            "maximum_required_context_tokens": input_tokens["max"] + output_tokens["max"],
        },
        "tools": {"enabled": False},
        "service_objectives": {
            "ttfa_p95_ms": float(experience["first_useful_response_seconds"]) * 1000,
            "answer_tokens_per_second_min": round(minimum_answer_tps, 3),
            "end_to_end_p95_ms": float(experience["complete_response_seconds"]) * 1000,
            "error_rate_max": float(experience.get("error_rate_max", 0.01)),
        },
        "quality": {
            "grounding_required": bool(quality.get("grounding_required", True)),
            "citation_required": bool(quality.get("citation_required", True)),
            "tool_call_correctness_required": False,
            "structured_output_required": bool(quality.get("structured_output_required", True)),
            "minimum_score": float(quality.get("minimum_score", 0.95)),
        },
        "response_contract": {
            "modes": ["probe", "assistant"],
            "default_mode": "assistant",
            "structured_result_required": True,
            "probe_max_tokens": min(128, output_tokens["p50"]),
            "assistant_max_tokens": output_tokens["max"],
        },
    }
    errors, warnings = validate_contract(contract)
    if errors:
        raise CapacityPlanningError("translated contract is invalid: " + "; ".join(errors))
    trace = [
        {"field": "request_profile.input_tokens", "provenance": "CALCULATED", "explanation": f"{context_mode} pages × {words_per_page:g} words/page × {tokens_per_word:g} tokens/word + {prompt_overhead} prompt tokens"},
        {"field": "request_profile.output_tokens", "provenance": "CALCULATED", "explanation": f"requested response words × {tokens_per_word:g} tokens/word"},
        {"field": "service_objectives.answer_tokens_per_second_min", "provenance": "CALCULATED", "explanation": "typical output divided by the completion window after first useful response"},
    ]
    trace.extend({"field": "contract", "provenance": "VALIDATION_WARNING", "explanation": warning} for warning in warnings)
    return contract, trace


def _metric_range(value: float, uncertainty: float, *, digits: int = 1) -> dict[str, float]:
    return {
        "low": round(max(0.0, value * (1 - uncertainty)), digits),
        "point": round(value, digits),
        "high": round(value * (1 + uncertainty), digits),
    }


def _estimate_candidate(hardware: dict[str, Any], model: dict[str, Any], bits: int, contract: dict[str, Any], max_power_w: float | None) -> dict[str, Any]:
    req = contract["request_profile"]
    concurrency = max(contract["traffic"]["concurrent_users"])
    context_p95 = req["input_tokens"]["p95"] + req["output_tokens"]["p95"]
    context_max = req["input_tokens"]["max"] + req["output_tokens"]["max"]
    weights_gib = model["total_parameters_b"] * 1e9 * bits / 8 / GIB * 1.08
    active_weights_gib = model["active_parameters_b"] * 1e9 * bits / 8 / GIB
    # Sparse models do not behave exactly like a dense model with the same
    # active parameter count. Routing, expert dispatch and traffic from the
    # resident expert set depend heavily on kernels and topology. This small,
    # explicit surcharge prevents a MoE archetype from receiving an identical
    # projection to a dense archetype while keeping the estimate conservative
    # enough for shortlist screening. It is not a measured model coefficient.
    is_moe = model["architecture"] == "mixture-of-experts"
    inactive_weights_gib = max(0.0, weights_gib / 1.08 - active_weights_gib)
    routing_overhead = 1.15 if is_moe else 1.0
    inactive_weight_traffic_fraction = 0.02 if is_moe else 0.0
    effective_decode_weights_gib = (
        active_weights_gib * routing_overhead
        + inactive_weights_gib * inactive_weight_traffic_fraction
    )
    kv_bytes_per_token = 2 * model["layers"] * model["kv_heads"] * model["head_dim"] * 2
    kv_gib = kv_bytes_per_token * context_p95 * concurrency / GIB
    runtime_gib = max(4.0, weights_gib * 0.12)
    required_gib = weights_gib + kv_gib + runtime_gib
    usable_gib = hardware["memory_gib"] * hardware["usable_memory_fraction"]
    blockers: list[str] = []
    if bits not in model["supported_weight_bits"]:
        blockers.append("WEIGHT_FORMAT_UNSUPPORTED_BY_ARCHETYPE")
    if context_max > model["maximum_context_tokens"]:
        blockers.append("MODEL_CONTEXT_TOO_SMALL")
    if required_gib > usable_gib:
        blockers.append("MEMORY_CAPACITY_EXCEEDED")
    if max_power_w is not None and hardware["power_w"] > max_power_w:
        blockers.append("POWER_LIMIT_EXCEEDED")
    assumptions = hardware["performance_assumptions"]
    anchor = assumptions.get("interactive_decode_anchor")
    if anchor and not is_moe:
        precision_factor = float(anchor["precision_factors"].get(str(bits), 1.0))
        parameter_ratio = float(anchor["active_parameters_b"]) / model["active_parameters_b"]
        single_user_decode_tps = (
            float(anchor["tokens_per_second_per_request"])
            * parameter_ratio ** float(anchor["parameter_scaling_exponent"])
            * precision_factor
        )
        projection_method = "VENDOR_INTERACTIVE_ANCHOR_SCALED"
        projection_source = {"name": anchor["source"], "url": anchor["url"]}
        uncertainty = 0.30
    else:
        single_user_decode_tps = (
            hardware["memory_bandwidth_gbps"]
            / max(effective_decode_weights_gib, 0.1)
            * assumptions["decode_efficiency"]
        )
        projection_method = "MEMORY_ROOFLINE_HEURISTIC"
        projection_source = None
        uncertainty = 0.60 if is_moe else 0.45
    # Continuous batching does not split a fixed single-request token rate by
    # the number of users. Requests advance together in a batch until the GPU
    # saturates. Model the measured per-request retention at concurrency 4 and
    # expose aggregate throughput as per-user throughput × active users.
    concurrency_steps = math.log2(max(1, concurrency)) / 2
    decode_retention = assumptions["decode_concurrency_retention_at_4"] ** concurrency_steps
    prefill_retention = assumptions["prefill_concurrency_retention_at_4"] ** concurrency_steps
    per_user_decode_tps = single_user_decode_tps * decode_retention
    aggregate_decode_tps = per_user_decode_tps * concurrency
    per_request_prefill_tps = (
        single_user_decode_tps
        * assumptions["prefill_multiplier"]
        * prefill_retention
    )
    ttfa_ms = assumptions["fixed_latency_ms"] + req["input_tokens"]["p95"] / max(per_request_prefill_tps, 1) * 1000
    e2e_ms = ttfa_ms + req["output_tokens"]["p95"] / max(per_user_decode_tps, 0.1) * 1000
    metrics = {
        "ttfa_p95_ms": {**_metric_range(ttfa_ms, uncertainty), "unit": "ms", "provenance": "ESTIMATED"},
        "end_to_end_p95_ms": {**_metric_range(e2e_ms, uncertainty), "unit": "ms", "provenance": "ESTIMATED"},
        "answer_tokens_per_second_per_user": {**_metric_range(per_user_decode_tps, uncertainty), "unit": "tokens/s", "provenance": "ESTIMATED"},
        "aggregate_answer_tokens_per_second": {**_metric_range(aggregate_decode_tps, uncertainty), "unit": "tokens/s", "provenance": "ESTIMATED"},
    }
    objectives = contract["service_objectives"]
    point_meets = (
        metrics["ttfa_p95_ms"]["point"] <= objectives["ttfa_p95_ms"]
        and metrics["end_to_end_p95_ms"]["point"] <= objectives["end_to_end_p95_ms"]
        and metrics["answer_tokens_per_second_per_user"]["point"] >= objectives["answer_tokens_per_second_min"]
    )
    conservative_meets = (
        metrics["ttfa_p95_ms"]["high"] <= objectives["ttfa_p95_ms"]
        and metrics["end_to_end_p95_ms"]["high"] <= objectives["end_to_end_p95_ms"]
        and metrics["answer_tokens_per_second_per_user"]["low"] >= objectives["answer_tokens_per_second_min"]
    )
    optimistic_meets = (
        metrics["ttfa_p95_ms"]["low"] <= objectives["ttfa_p95_ms"]
        and metrics["end_to_end_p95_ms"]["low"] <= objectives["end_to_end_p95_ms"]
        and metrics["answer_tokens_per_second_per_user"]["high"] >= objectives["answer_tokens_per_second_min"]
    )
    headroom = (usable_gib - required_gib) / usable_gib
    if blockers:
        status = "NOT_FEASIBLE"
    elif conservative_meets and headroom >= 0.15:
        status = "STRONG_FIT"
    elif point_meets:
        status = "CONDITIONAL_FIT"
    elif optimistic_meets:
        status = "BORDERLINE_FIT"
    else:
        status = "UNLIKELY_FIT"
    latency_ratio = objectives["end_to_end_p95_ms"] / max(metrics["end_to_end_p95_ms"]["point"], 1)
    speed_ratio = metrics["answer_tokens_per_second_per_user"]["point"] / objectives["answer_tokens_per_second_min"]
    score = -1000 if blockers else round(50 * max(-1, headroom) + 25 * min(2, latency_ratio) + 25 * min(2, speed_ratio), 3)
    return {
        "candidate_id": f"{hardware['id']}--{model['id']}--{bits}bit",
        "hardware": {"id": hardware["id"], "name": hardware["name"], "class": hardware["deployment_class"], "power_w": hardware["power_w"], "specification_provenance": hardware["specification_provenance"]},
        "model": {"id": model["id"], "name": model["name"], "architecture": model["architecture"], "planning_archetype": True},
        "weight_bits": bits,
        "screening_status": status,
        "final_recommendation_status": "MEASUREMENT_REQUIRED",
        "screening_score": score,
        "blockers": blockers,
        "assessment": {
            "method": "UNCERTAINTY_BAND_AND_HARD_GATES_V1",
            "rationale": {
                "STRONG_FIT": "Even the conservative performance band meets every target and memory headroom is at least 15%.",
                "CONDITIONAL_FIT": "The point estimate meets every target, but the uncertainty band or memory margin requires measurement.",
                "BORDERLINE_FIT": "Only the optimistic edge of the estimate meets every target; benchmark priority is low unless this model class has a quality advantage.",
                "UNLIKELY_FIT": "Even the optimistic performance band misses at least one target.",
                "NOT_FEASIBLE": "A hard capacity, context, precision or power constraint is violated.",
            }[status],
            "hard_gates_pass": not blockers,
            "conservative_band_meets_all_slos": conservative_meets,
            "point_estimate_meets_all_slos": point_meets,
            "optimistic_band_meets_all_slos": optimistic_meets,
            "memory_headroom_fraction": round(headroom, 4),
            "gates": {
                "memory": {"actual_gib": round(required_gib, 2), "maximum_gib": round(usable_gib, 2), "pass": required_gib <= usable_gib},
                "context": {"actual_tokens": context_max, "maximum_tokens": model["maximum_context_tokens"], "pass": context_max <= model["maximum_context_tokens"]},
                "power": {"actual_w": hardware["power_w"], "maximum_w": max_power_w, "pass": max_power_w is None or hardware["power_w"] <= max_power_w},
                "ttfa_point": {"actual_ms": metrics["ttfa_p95_ms"]["point"], "maximum_ms": objectives["ttfa_p95_ms"], "pass": metrics["ttfa_p95_ms"]["point"] <= objectives["ttfa_p95_ms"]},
                "end_to_end_point": {"actual_ms": metrics["end_to_end_p95_ms"]["point"], "maximum_ms": objectives["end_to_end_p95_ms"], "pass": metrics["end_to_end_p95_ms"]["point"] <= objectives["end_to_end_p95_ms"]},
                "answer_speed_point": {"actual_tps": metrics["answer_tokens_per_second_per_user"]["point"], "minimum_tps": objectives["answer_tokens_per_second_min"], "pass": metrics["answer_tokens_per_second_per_user"]["point"] >= objectives["answer_tokens_per_second_min"]},
            },
        },
        "capacity": {
            "usable_memory_gib": round(usable_gib, 2),
            "estimated_weight_memory_gib": round(weights_gib, 2),
            "estimated_kv_cache_gib_at_p95_concurrency": round(kv_gib, 2),
            "estimated_runtime_reserve_gib": round(runtime_gib, 2),
            "estimated_total_required_gib": round(required_gib, 2),
            "estimated_headroom_gib": round(usable_gib - required_gib, 2),
            "provenance": "CALCULATED",
        },
        "performance_projection": {
            "confidence": "MEDIUM_LOW" if anchor and not is_moe else "LOW",
            "uncertainty_fraction": uncertainty,
            "assumption_provenance": "VENDOR_BENCHMARK_SCALED" if anchor and not is_moe else assumptions["provenance"],
            "calibration_status": "SCALED_FROM_VENDOR_INTERACTIVE_ANCHOR" if anchor and not is_moe else "UNCALIBRATED_FOR_THIS_HARDWARE_MODEL_PAIR",
            "projection_method": projection_method,
            "projection_source": projection_source,
            "metric_basis": {
                "decode": (
                    "VENDOR_INTERACTIVE_DECODE_ANCHOR_SCALED"
                    if anchor and not is_moe
                    else "MEMORY_ROOFLINE_SRH_HEURISTIC"
                ),
                "ttfa": "SRH_PREFILL_HEURISTIC",
                "memory": "ARCHETYPE_CAPACITY_CALCULATION",
            },
            "concurrency_model": {
                "single_user_decode_tps": round(single_user_decode_tps, 3),
                "decode_retention_fraction": round(decode_retention, 4),
                "per_request_prefill_tps": round(per_request_prefill_tps, 3),
                "basis": "continuous batching per-request retention",
            },
            "architecture_adjustment": {
                "routing_overhead_multiplier": routing_overhead,
                "inactive_weight_traffic_fraction": inactive_weight_traffic_fraction,
                "effective_decode_weights_gib": round(effective_decode_weights_gib, 3),
                "provenance": "SRH_HEURISTIC",
            },
            "metrics": metrics,
            "provenance": "ESTIMATED",
            "quality": "NOT_SIMULATED_MEASUREMENT_REQUIRED",
        },
    }


def _shortlist(candidates: list[dict[str, Any]], size: int = 3) -> list[str]:
    eligible = [candidate for candidate in candidates if candidate["screening_status"] != "NOT_FEASIBLE"]
    status_rank = {"STRONG_FIT": 0, "CONDITIONAL_FIT": 1, "BORDERLINE_FIT": 2, "UNLIKELY_FIT": 3}
    eligible.sort(key=lambda value: (status_rank[value["screening_status"]], -value["screening_score"], value["candidate_id"]))
    selected: list[dict[str, Any]] = []
    hardware_seen: set[str] = set()
    model_ids = list(dict.fromkeys(candidate["model"]["id"] for candidate in eligible))
    for model_id in model_ids:
        options = [candidate for candidate in eligible if candidate["model"]["id"] == model_id]
        best = options[0]
        # Hardware diversity is useful only among genuinely comparable options.
        # Never downgrade a model class to a weaker screening tier merely to
        # display another device in the shortlist.
        comparable_diverse = next((
            value for value in options
            if value["hardware"]["id"] not in hardware_seen
            and value["screening_status"] == best["screening_status"]
            and value["screening_score"] >= best["screening_score"] - 15
        ), None)
        candidate = comparable_diverse or best
        selected.append(candidate)
        hardware_seen.add(candidate["hardware"]["id"])
        if len(selected) == size:
            return [value["candidate_id"] for value in selected]
    for candidate in eligible:
        hardware_id = candidate["hardware"]["id"]
        if hardware_id not in hardware_seen and candidate not in selected:
            selected.append(candidate)
            hardware_seen.add(hardware_id)
        if len(selected) == size:
            break
    for candidate in eligible:
        if candidate not in selected:
            selected.append(candidate)
        if len(selected) == size:
            break
    return [candidate["candidate_id"] for candidate in selected]


def build_capacity_plan(intake: dict[str, Any], catalogs: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    contract, translation_trace = translate_intake(intake)
    catalogs = catalogs or load_catalogs()
    hardware_by_id = {item["id"]: item for item in catalogs["hardware"]["items"]}
    model_by_id = {item["id"]: item for item in catalogs["models"]["items"]}
    exploration = intake["exploration"]
    unknown_hardware = sorted(set(exploration["hardware_ids"]) - hardware_by_id.keys())
    unknown_models = sorted(set(exploration["model_ids"]) - model_by_id.keys())
    if unknown_hardware or unknown_models:
        raise CapacityPlanningError(f"unknown catalog ids: hardware={unknown_hardware}, models={unknown_models}")
    max_power = intake.get("deployment", {}).get("maximum_device_power_w")
    if max_power is not None:
        max_power = _positive(max_power, "deployment.maximum_device_power_w")
    candidates = [
        _estimate_candidate(hardware_by_id[hardware_id], model_by_id[model_id], int(bits), contract, max_power)
        for hardware_id in exploration["hardware_ids"]
        for model_id in exploration["model_ids"]
        for bits in exploration["weight_bits"]
    ]
    candidates.sort(key=lambda value: (-value["screening_score"], value["candidate_id"]))
    selected_hardware = set(exploration["hardware_ids"])
    relevant_evidence = [item for item in catalogs["evidence"]["items"] if item.get("hardware_id") in selected_hardware]
    experiment_plan = build_plan(contract)
    shortlist = _shortlist(candidates, size=min(8, max(3, len(exploration["model_ids"]))))
    return {
        "schema": OUTPUT_SCHEMA,
        "planner_version": PLANNER_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "assessment_id": intake["id"],
        "client_inputs": intake,
        "translated_workload_contract": contract,
        "translation_trace": translation_trace,
        "catalog_versions": {key: value["version"] for key, value in catalogs.items()},
        "integrity": {
            "client_intake_sha256": canonical_sha256(intake),
            "translated_contract_sha256": canonical_sha256(contract),
            "catalog_sha256": {key: canonical_sha256(value) for key, value in catalogs.items()},
        },
        "projection_model": {
            "version": PLANNER_VERSION,
            "weight_memory": "total_parameters × weight_bits / 8 × 1.08 planning overhead",
            "kv_cache": "2 × layers × KV heads × head dimension × 2 bytes × p95 context × concurrency",
            "decode": "vendor interactive anchor when available; otherwise memory bandwidth / effective active weight traffic × explicit efficiency",
            "concurrency": "continuous-batching per-request retention; aggregate throughput = per-request rate × concurrent users",
            "prefill": "single-request decode × explicit prefill multiplier × concurrency retention",
            "range": "vendor-anchored dense estimate ±30%; unanchored dense ±45%; sparse MoE ±60%",
            "provenance": "MIXED_VENDOR_ANCHOR_AND_SRH_HEURISTIC",
        },
        "decision_boundary": {
            "status": "PRELIMINARY_CAPACITY_SCREENING",
            "message": "The shortlist uses calculated capacity and mixed-confidence performance projections. It is not a deployment recommendation.",
            "measured_evidence_required": True,
            "quality_is_not_simulated": True,
            "compatible_with_profile_recommender": False,
            "compatible_with_deployment_recommender_after_measurement": True,
        },
        "screening_summary": {
            "candidate_count": len(candidates),
            "feasible_count": sum(candidate["screening_status"] != "NOT_FEASIBLE" for candidate in candidates),
            "strong_fit_count": sum(candidate["screening_status"] == "STRONG_FIT" for candidate in candidates),
            "conditional_fit_count": sum(candidate["screening_status"] == "CONDITIONAL_FIT" for candidate in candidates),
            "borderline_fit_count": sum(candidate["screening_status"] == "BORDERLINE_FIT" for candidate in candidates),
            "shortlist_candidate_ids": shortlist,
        },
        "candidates": candidates,
        "historical_evidence_coverage": relevant_evidence,
        "benchmark_handoff": {
            "candidate_ids": shortlist,
            "requested_model_families": exploration.get("requested_model_families", []),
            "experiment_plan": experiment_plan,
            "required_evidence_provenance": ["SRH_MEASURED", "CUSTOMER_SITE_MEASURED"],
            "measurement_kpis": {
                "user_experience": [
                    "time_to_first_useful_answer_p50_ms",
                    "time_to_first_useful_answer_p95_ms",
                    "end_to_end_p50_ms",
                    "end_to_end_p95_ms",
                    "answer_tokens_per_second_per_user",
                    "request_error_rate",
                ],
                "domain_quality": [
                    "critical_requirement_recall",
                    "grounded_answer_score",
                    "citation_correctness",
                    "critical_omission_rate",
                    "unsupported_claim_rate",
                    "draft_rubric_score",
                ],
                "rag_pipeline": [
                    "ocr_success_rate",
                    "ocr_latency_ms",
                    "retrieval_recall_at_k",
                    "retrieval_latency_ms",
                    "reranking_latency_ms",
                ],
                "infrastructure": [
                    "accelerator_memory_peak_gib",
                    "host_memory_peak_gib",
                    "sustained_concurrency",
                    "device_power_w",
                    "model_load_seconds",
                    "stability_error_count",
                ],
            },
            "current_harness_coverage": [
                "time_to_first_useful_answer_p95_ms",
                "end_to_end_p95_ms",
                "answer_tokens_per_second_per_user",
                "request_error_rate",
                "versioned_task_quality",
                "observed_runtime_identity",
            ],
            "next_action": "Resolve each archetype to an exact licensed model/runtime profile and run the scenario pack. Use the Profile Recommender to tune profiles within one fixed deployment, or the Deployment Recommender to compare observed systems across hardware and exact models.",
        },
        "limitations": [
            "Model archetypes are sizing devices, not named-model quality claims.",
            "Performance ranges are planning estimates with low confidence until calibrated by comparable measurements.",
            "OCR, retrieval, reranking, networking, storage and application overhead are not included in the current latency estimate.",
            "Vendor specifications describe hardware capacity; they are not SRH performance evidence.",
            "A final recommendation requires a client scenario pack and observed evidence from the shortlisted candidates.",
        ],
    }


def render_markdown(plan: dict[str, Any]) -> str:
    contract = plan["translated_workload_contract"]
    summary = plan["screening_summary"]
    shortlist = set(summary["shortlist_candidate_ids"])
    lines = [
        "# SRH Capacity Plan",
        "",
        f"Assessment: `{plan['assessment_id']}`",
        "",
        "**Preliminary capacity screening — measurement required before deployment recommendation.**",
        "",
        "## Translated workload",
        "",
        f"- Concurrent users: {max(contract['traffic']['concurrent_users'])}",
        f"- Input tokens p50 / p95 / max: {contract['request_profile']['input_tokens']['p50']} / {contract['request_profile']['input_tokens']['p95']} / {contract['request_profile']['input_tokens']['max']}",
        f"- Output tokens p50 / p95 / max: {contract['request_profile']['output_tokens']['p50']} / {contract['request_profile']['output_tokens']['p95']} / {contract['request_profile']['output_tokens']['max']}",
        f"- First useful response p95 target: {contract['service_objectives']['ttfa_p95_ms'] / 1000:g} s",
        f"- Complete response p95 target: {contract['service_objectives']['end_to_end_p95_ms'] / 1000:g} s",
        "",
        "## Candidate screening",
        "",
        "| Shortlist | Hardware | Model archetype | Bits | Status | Memory required / usable | Estimated TTFA p95 | Estimated E2E p95 |",
        "|---|---|---|---:|---|---:|---:|---:|",
    ]
    for candidate in plan["candidates"]:
        capacity = candidate["capacity"]
        metrics = candidate["performance_projection"]["metrics"]
        lines.append(
            f"| {'Yes' if candidate['candidate_id'] in shortlist else ''} | {candidate['hardware']['name']} | {candidate['model']['name']} | {candidate['weight_bits']} | {candidate['screening_status']} | "
            f"{capacity['estimated_total_required_gib']} / {capacity['usable_memory_gib']} GiB | {metrics['ttfa_p95_ms']['low']}–{metrics['ttfa_p95_ms']['high']} ms | {metrics['end_to_end_p95_ms']['low']}–{metrics['end_to_end_p95_ms']['high']} ms |"
        )
    lines.extend(["", "## Required next step", "", plan["benchmark_handoff"]["next_action"], "", "## Limitations", ""])
    lines.extend(f"- {value}" for value in plan["limitations"])
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build an SRH preliminary capacity plan from client-language inputs")
    parser.add_argument("intake", help="Path to an srh.client-intake.v1 JSON file")
    parser.add_argument("--output", required=True, help="Capacity-plan JSON output")
    parser.add_argument("--markdown", help="Optional human-readable report")
    args = parser.parse_args()
    intake = json.loads(Path(args.intake).read_text(encoding="utf-8"))
    plan = build_capacity_plan(intake)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    if args.markdown:
        markdown = Path(args.markdown)
        markdown.parent.mkdir(parents=True, exist_ok=True)
        markdown.write_text(render_markdown(plan), encoding="utf-8")
    print("SRH CAPACITY PLANNER")
    print("=" * 60)
    print(f"Assessment : {plan['assessment_id']}")
    print(f"Candidates : {plan['screening_summary']['candidate_count']}")
    print(f"Feasible   : {plan['screening_summary']['feasible_count']}")
    print(f"Shortlist  : {', '.join(plan['screening_summary']['shortlist_candidate_ids']) or 'none'}")
    print("Decision   : preliminary screening; measured evidence required")


if __name__ == "__main__":
    main()
