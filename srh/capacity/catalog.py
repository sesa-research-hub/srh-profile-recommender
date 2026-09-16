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
    return value


def load_catalogs() -> dict[str, dict[str, Any]]:
    return {
        "hardware": _load("hardware-v1.json", "srh.hardware-catalog.v1"),
        "models": _load("model-archetypes-v1.json", "srh.model-archetype-catalog.v1"),
        "evidence": _load("evidence-v1.json", "srh.capacity-evidence-catalog.v1"),
        "reference_models": _load("reference-models-v1.json", "srh.reference-model-catalog.v1"),
    }
