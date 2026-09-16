# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Sesa Research Hub
"""Build sourced planning projections for named models that are not installed."""

from __future__ import annotations

from typing import Any

from .catalog import load_catalogs
from .planner import CapacityPlanningError, _estimate_candidate


def simulate_reference_model(
    model_id: str,
    contract: dict[str, Any],
    hardware_ids: list[str],
    weight_bits: int,
    maximum_power_w: float | None,
) -> dict[str, Any]:
    catalogs = load_catalogs()
    models = {item["id"]: item for item in catalogs["reference_models"]["items"]}
    hardware = {item["id"]: item for item in catalogs["hardware"]["items"]}
    if model_id not in models:
        raise CapacityPlanningError("unknown reference model")
    unknown = sorted(set(hardware_ids) - hardware.keys())
    if unknown:
        raise CapacityPlanningError(f"unknown hardware ids: {unknown}")
    model = models[model_id]
    candidates = [
        _estimate_candidate(hardware[hardware_id], model, weight_bits, contract, maximum_power_w)
        for hardware_id in hardware_ids
    ]
    candidates.sort(key=lambda value: (-value["screening_score"], value["candidate_id"]))
    return {
        "schema": "srh.reference-model-simulation.v1",
        "model": model,
        "candidates": candidates,
        "evidence_boundary": {
            "capacity_and_performance": "SRH low-confidence planning estimate",
            "published_benchmarks": "vendor-reported reference evidence on different tasks and infrastructure",
            "quality_for_client_workload": "NOT_MEASURED",
            "deployment_recommendation": False,
        },
        "next_action": "Install an exact checkpoint on compatible hardware and run the same local readiness and client scenario benchmarks.",
    }
