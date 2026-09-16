#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Sesa Research Hub
"""Compare observed deployments across hardware and model identities."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any

from srh.profiles.capture import canonical_sha256
from srh.recommendation.engine import candidate, require
from srh.workloads.contract import validate_contract


SCHEMA = "srh.deployment-recommendation.v1"
MANIFEST_SCHEMA = "srh.deployment-candidates.v1"


def _manifest_by_profile(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    require(manifest.get("schema") == MANIFEST_SCHEMA, "unsupported deployment candidate manifest")
    require(manifest.get("policy") in {"performance_first", "cost_first"}, "unsupported deployment policy")
    values = manifest.get("candidates")
    require(isinstance(values, list) and len(values) >= 2, "at least two deployment candidates are required")
    result: dict[str, dict[str, Any]] = {}
    for value in values:
        require(isinstance(value, dict), "invalid deployment candidate metadata")
        profile_id = value.get("profile_id")
        require(isinstance(profile_id, str) and profile_id.startswith("srh-"), "invalid deployment profile id")
        require(profile_id not in result, "duplicate deployment profile metadata")
        require(isinstance(value.get("label"), str) and value["label"], "deployment candidate label is required")
        status = value.get("license_review_status")
        require(status in {"approved", "review_required", "restricted"}, "invalid license review status")
        cost = value.get("estimated_three_year_cost_eur")
        if cost is not None:
            require(type(cost) in (int, float) and math.isfinite(cost) and cost >= 0, "invalid three-year cost")
            require(value.get("cost_provenance") in {"supplier_quote", "calculated", "customer_provided"}, "cost provenance is required")
        power = value.get("device_power_w")
        if power is not None:
            require(type(power) in (int, float) and math.isfinite(power) and power > 0, "invalid device power")
        result[profile_id] = value
    return result


def recommend_deployment(evidences: list[tuple[str, dict[str, Any]]], contract: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    errors, _ = validate_contract(contract)
    require(not errors, "invalid workload contract: " + "; ".join(errors))
    metadata = _manifest_by_profile(manifest)
    rows: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    cohort_signatures: list[dict[str, Any]] = []
    for source, evidence in evidences:
        try:
            signature, row = candidate(evidence, contract)
            require(row["profile_id"] not in [item["profile_id"] for item in rows], "duplicate observed deployment profile")
            meta = metadata.get(row["profile_id"])
            require(meta is not None, "deployment candidate metadata missing for observed profile")
            cohort = {key: signature[key] for key in ("experiment", "scenario", "comparison", "protocol")}
            row["source_evidence"] = source
            row["evidence_sha256"] = canonical_sha256(evidence)
            row["deployment"] = {
                "label": meta["label"],
                "hardware": signature["hardware"],
                "model": signature["model"],
                "engine": signature["engine"],
                "image_id": signature["image_id"],
                "license_review_status": meta["license_review_status"],
                "estimated_three_year_cost_eur": meta.get("estimated_three_year_cost_eur"),
                "cost_provenance": meta.get("cost_provenance"),
                "device_power_w": meta.get("device_power_w"),
            }
            if meta["license_review_status"] != "approved":
                row["eligible"] = False
                row["blocking_objectives"].append("license_review_status")
            rows.append(row)
            cohort_signatures.append(cohort)
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            rejected.append({"source_evidence": source, "reason": str(exc)})
    comparable = bool(cohort_signatures) and all(value == cohort_signatures[0] for value in cohort_signatures)
    mismatch_fields = sorted({key for signature in cohort_signatures[1:] for key in cohort_signatures[0] if signature[key] != cohort_signatures[0][key]}) if cohort_signatures else []
    sufficient = comparable and len(rows) >= 2 and not rejected
    eligible = [row for row in rows if row["eligible"]]
    policy = manifest["policy"]
    missing_cost = policy == "cost_first" and any(row["deployment"]["estimated_three_year_cost_eur"] is None for row in eligible)
    if policy == "cost_first":
        ranked = sorted(eligible, key=lambda row: (
            row["deployment"]["estimated_three_year_cost_eur"] if row["deployment"]["estimated_three_year_cost_eur"] is not None else math.inf,
            row["checks"]["ttfa_p95_ms"]["actual"],
            row["checks"]["end_to_end_p95_ms"]["actual"],
            row["profile_sha256"],
        ))
    else:
        ranked = sorted(eligible, key=lambda row: (
            row["checks"]["ttfa_p95_ms"]["actual"],
            row["checks"]["end_to_end_p95_ms"]["actual"],
            -row["checks"]["answer_tokens_per_second_min"]["actual"],
            row["deployment"]["estimated_three_year_cost_eur"] if row["deployment"]["estimated_three_year_cost_eur"] is not None else math.inf,
            row["profile_sha256"],
        ))
    if not sufficient or missing_cost:
        verdict = "INSUFFICIENT_EVIDENCE"
    elif ranked:
        verdict = "RECOMMENDED"
    else:
        verdict = "NO_DEPLOYMENT_MEETS_REQUIREMENTS"
    selected = ranked[0] if verdict == "RECOMMENDED" else None
    return {
        "schema": SCHEMA,
        "engine_version": "0.1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "workload_id": contract["id"],
        "contract_sha256": canonical_sha256(contract),
        "candidate_manifest_sha256": canonical_sha256(manifest),
        "verdict": verdict,
        "recommended_profile_id": selected["profile_id"] if selected else None,
        "recommended_label": selected["deployment"]["label"] if selected else None,
        "policy": policy,
        "policy_explanation": "Quality, all SLOs and approved license review are mandatory; then " + ("three-year cost, TTFA and E2E" if policy == "cost_first" else "TTFA, E2E, answer throughput and available cost"),
        "comparison_scope": "same workload experiment, scenario, ground truth, evaluator and protocol; hardware, exact model, engine and image may differ",
        "minimum_observed_deployments": 2,
        "comparable": comparable,
        "incompatible_fields": mismatch_fields,
        "candidates": rows,
        "rejected_evidence": rejected,
        "ranking": [row["profile_id"] for row in ranked] if sufficient and not missing_cost else [],
        "recommended_next_action": (
            "Run the remaining workload experiments and an acceptance test on the target site" if verdict == "RECOMMENDED" else
            "Complete comparable observations and required candidate metadata" if verdict == "INSUFFICIENT_EVIDENCE" else
            "Revise the application, model or infrastructure shortlist and benchmark again"
        ),
        "limitations": [
            "The verdict applies only to the common measured experiment and scenario.",
            "Cross-model quality is valid only for the versioned evaluator and ground truth supplied.",
            "License approval and cost provenance are human-supplied decision metadata, not inferred by the engine.",
            "A customer-site acceptance test remains required before production commitment.",
        ],
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# SRH Deployment Recommendation", "", f"**Verdict: {report['verdict']}**", "",
        f"Recommended deployment: **{report['recommended_label'] or 'none'}**", "",
        f"Policy: {report['policy_explanation']}", "",
        "| Deployment | Profile | Model | Quality | TTFA p95 | E2E p95 | License | Eligible |",
        "|---|---|---|---:|---:|---:|---|---|",
    ]
    for row in report["candidates"]:
        checks = row["checks"]
        deployment = row["deployment"]
        lines.append(
            f"| {deployment['label']} | `{row['profile_id']}` | {deployment['model'].get('snapshot', '?')} | "
            f"{checks['quality_minimum_score']['actual']} | {checks['ttfa_p95_ms']['actual']} ms | "
            f"{checks['end_to_end_p95_ms']['actual']} ms | {deployment['license_review_status']} | {row['eligible']} |"
        )
    lines.extend(["", "## Next action", "", report["recommended_next_action"], "", "## Limitations", ""])
    lines.extend(f"- {value}" for value in report["limitations"])
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("contract", type=Path)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("evidence", type=Path, nargs="+")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args()
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    evidences = [(str(path.resolve()), json.loads(path.read_text(encoding="utf-8"))) for path in args.evidence]
    report = recommend_deployment(evidences, contract, manifest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(render_markdown(report), encoding="utf-8")
    print(report["verdict"])
    print("Recommended:", report["recommended_label"])
    print("Decision evidence:", args.output)


if __name__ == "__main__":
    main()
