#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
#
# Copyright 2026 Sesa Research Hub
#
# SRH Private AI - Workload Contract loader and semantic validator

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


SUPPORTED_SCHEMA = "srh.workload-contract.v1"


class WorkloadContractError(ValueError):
    """Raised when an SRH workload contract is not operationally valid."""


def _validate_distribution(
    name: str,
    dist: Any,
    errors: list[str],
) -> None:
    if not isinstance(dist, dict):
        errors.append(f"{name} must be an object")
        return

    for key in ("p50", "p95"):
        value = dist.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            errors.append(f"{name}.{key} must be a positive integer")

    maximum = dist.get("max")
    if maximum is not None:
        if (
            not isinstance(maximum, int)
            or isinstance(maximum, bool)
            or maximum < 1
        ):
            errors.append(f"{name}.max must be a positive integer")

    p50 = dist.get("p50")
    p95 = dist.get("p95")

    if (
        isinstance(p50, int)
        and isinstance(p95, int)
        and p50 > p95
    ):
        errors.append(
            f"{name} is not monotonic: p50 ({p50}) > p95 ({p95})"
        )

    if (
        isinstance(p95, int)
        and isinstance(maximum, int)
        and p95 > maximum
    ):
        errors.append(
            f"{name} is not monotonic: p95 ({p95}) > max ({maximum})"
        )


def validate_contract(
    contract: dict[str, Any],
) -> tuple[list[str], list[str]]:
    """
    Validate semantics needed by SRH experiment planning.

    Returns:
        (errors, warnings)
    """

    errors: list[str] = []
    warnings: list[str] = []

    if contract.get("schema") != SUPPORTED_SCHEMA:
        errors.append(
            "unsupported schema: "
            f"{contract.get('schema')!r}; expected {SUPPORTED_SCHEMA!r}"
        )

    workload_id = contract.get("id")
    if not isinstance(workload_id, str) or not workload_id.strip():
        errors.append("id must be a non-empty string")

    workload_class = contract.get("workload_class")
    if not isinstance(workload_class, str) or not workload_class:
        errors.append("workload_class must be defined")

    traffic = contract.get("traffic")
    if not isinstance(traffic, dict):
        errors.append("traffic must be an object")
        traffic = {}

    concurrency = traffic.get("concurrent_users")

    if not isinstance(concurrency, list) or not concurrency:
        errors.append("traffic.concurrent_users must be a non-empty array")
    else:
        invalid = [
            value
            for value in concurrency
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 1
            )
        ]

        if invalid:
            errors.append(
                "traffic.concurrent_users must contain positive integers"
            )
        else:
            if len(concurrency) != len(set(concurrency)):
                errors.append(
                    "traffic.concurrent_users contains duplicate values"
                )

            if concurrency != sorted(concurrency):
                warnings.append(
                    "traffic.concurrent_users is not sorted; "
                    "experiment planners should normalize it"
                )

    request_profile = contract.get("request_profile")
    if not isinstance(request_profile, dict):
        errors.append("request_profile must be an object")
        request_profile = {}

    input_tokens = request_profile.get("input_tokens")
    output_tokens = request_profile.get("output_tokens")

    _validate_distribution(
        "request_profile.input_tokens",
        input_tokens,
        errors,
    )

    _validate_distribution(
        "request_profile.output_tokens",
        output_tokens,
        errors,
    )

    streaming = request_profile.get("streaming", True)

    service_objectives = contract.get("service_objectives")
    if not isinstance(service_objectives, dict):
        errors.append("service_objectives must be an object")
        service_objectives = {}

    if not service_objectives:
        errors.append(
            "at least one service objective is required; "
            "otherwise the recommender has no operational target"
        )

    if (
        not streaming
        and "ttfa_p95_ms" in service_objectives
    ):
        warnings.append(
            "ttfa_p95_ms is defined for a non-streaming workload; "
            "its user-experience meaning may be limited"
        )

    context_behavior = contract.get("context_behavior", {})

    if context_behavior is not None:
        if not isinstance(context_behavior, dict):
            errors.append("context_behavior must be an object")
            context_behavior = {}

    max_context = context_behavior.get(
        "maximum_required_context_tokens"
    )

    if max_context is not None:
        input_max = (
            input_tokens.get("max")
            if isinstance(input_tokens, dict)
            else None
        )

        output_max = (
            output_tokens.get("max")
            if isinstance(output_tokens, dict)
            else None
        )

        if (
            isinstance(input_max, int)
            and isinstance(output_max, int)
            and input_max + output_max > max_context
        ):
            errors.append(
                "maximum_required_context_tokens is smaller than "
                "input_tokens.max + output_tokens.max "
                f"({max_context} < {input_max + output_max})"
            )

    tools = contract.get("tools", {})

    if tools is not None:
        if not isinstance(tools, dict):
            errors.append("tools must be an object")
            tools = {}

    tools_enabled = tools.get("enabled", False)
    calls_distribution = tools.get("calls_per_request")

    if tools_enabled and calls_distribution is None:
        warnings.append(
            "tools are enabled but calls_per_request is not defined; "
            "the planner cannot model tool pressure accurately"
        )

    if not tools_enabled and calls_distribution is not None:
        errors.append(
            "calls_per_request is defined while tools.enabled is false"
        )

    quality = contract.get("quality", {})

    if quality is not None:
        if not isinstance(quality, dict):
            errors.append("quality must be an object")
            quality = {}

    if (
        quality.get("citation_required") is True
        and quality.get("grounding_required") is not True
    ):
        warnings.append(
            "citations are required but grounding_required is not true"
        )

    if (
        workload_class == "rag"
        and quality.get("grounding_required") is not True
    ):
        warnings.append(
            "RAG workload does not explicitly require grounding"
        )

    if (
        workload_class == "agent"
        and not tools_enabled
    ):
        warnings.append(
            "agent workload has tools.enabled=false"
        )

    return errors, warnings


def load_contract(path: str | Path) -> dict[str, Any]:
    path = Path(path)

    try:
        contract = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkloadContractError(
            f"cannot load workload contract {path}: {exc}"
        ) from exc

    if not isinstance(contract, dict):
        raise WorkloadContractError(
            "workload contract root must be a JSON object"
        )

    errors, _ = validate_contract(contract)

    if errors:
        raise WorkloadContractError(
            "; ".join(errors)
        )

    return contract


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate an SRH Workload Contract"
    )

    parser.add_argument(
        "contract",
        help="Path to workload contract JSON",
    )

    args = parser.parse_args()

    path = Path(args.contract)

    try:
        contract = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"INVALID: {exc}")
        raise SystemExit(1)

    if not isinstance(contract, dict):
        print("INVALID: workload contract root must be a JSON object")
        raise SystemExit(1)

    errors, warnings = validate_contract(contract)

    print("SRH WORKLOAD CONTRACT")
    print("=" * 60)
    print(f"File   : {path}")
    print(f"ID     : {contract.get('id')}")
    print(f"Class  : {contract.get('workload_class')}")
    print()

    for warning in warnings:
        print(f"WARNING: {warning}")

    for error in errors:
        print(f"ERROR  : {error}")

    if errors:
        print()
        print(f"INVALID ({len(errors)} error(s))")
        raise SystemExit(1)

    print()
    print(
        f"VALID ({len(warnings)} warning(s))"
    )


if __name__ == "__main__":
    main()
