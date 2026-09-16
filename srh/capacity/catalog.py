# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Sesa Research Hub
"""Versioned, local catalogs used by the capacity planner."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


CATALOG_DIR = Path(__file__).resolve().parent / "catalog"


class CatalogError(ValueError):
    """Raised when a bundled capacity catalog is malformed."""


def _load(name: str, expected_schema: str) -> dict[str, Any]:
    path = CATALOG_DIR / name
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CatalogError(f"cannot load {path}: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema") != expected_schema:
        raise CatalogError(f"unexpected schema in {path}")
    if not isinstance(value.get("items"), list) or not value["items"]:
        raise CatalogError(f"empty catalog {path}")
    ids = [item.get("id") for item in value["items"]]
    if any(not isinstance(item_id, str) or not item_id for item_id in ids):
        raise CatalogError(f"invalid item id in {path}")
    if len(ids) != len(set(ids)):
        raise CatalogError(f"duplicate item id in {path}")
    if expected_schema == "srh.hardware-catalog.v1":
        for item in value["items"]:
            assumptions = item.get("performance_assumptions")
            if not isinstance(assumptions, dict):
                raise CatalogError(f"missing performance assumptions for {item['id']}")
            positive = ("decode_efficiency", "prefill_multiplier", "fixed_latency_ms")
            fractions = ("decode_concurrency_retention_at_4", "prefill_concurrency_retention_at_4")
            if any(not isinstance(assumptions.get(key), (int, float)) or assumptions[key] <= 0 for key in positive):
                raise CatalogError(f"invalid performance coefficient for {item['id']}")
            if any(not isinstance(assumptions.get(key), (int, float)) or not 0 < assumptions[key] <= 1 for key in fractions):
                raise CatalogError(f"invalid concurrency retention for {item['id']}")
            anchor = assumptions.get("interactive_decode_anchor")
            if anchor is not None:
                required = ("active_parameters_b", "tokens_per_second_per_request", "parameter_scaling_exponent", "precision_factors", "source", "url")
                if not isinstance(anchor, dict) or any(key not in anchor for key in required):
                    raise CatalogError(f"invalid interactive decode anchor for {item['id']}")
    return value


def load_catalogs() -> dict[str, dict[str, Any]]:
    return {
        "hardware": _load("hardware-v1.json", "srh.hardware-catalog.v1"),
        "models": _load("model-archetypes-v1.json", "srh.model-archetype-catalog.v1"),
        "evidence": _load("evidence-v1.json", "srh.capacity-evidence-catalog.v1"),
        "reference_models": _load("reference-models-v1.json", "srh.reference-model-catalog.v1"),
    }
