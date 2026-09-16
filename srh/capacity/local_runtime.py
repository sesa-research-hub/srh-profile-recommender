#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Sesa Research Hub
"""Discover local OpenAI-compatible models and run a scoped readiness benchmark."""

from __future__ import annotations

import concurrent.futures
import json
import math
import re
import statistics
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlparse
import urllib.request

from srh.benchmark import stream_chat


DEFAULT_ENDPOINTS = ("http://127.0.0.1:18300/v1", "http://127.0.0.1:8000/v1")
PROFILES = {
    "quick": {"input_cap": 2048, "output_cap": 256},
    "representative": {"input_cap": 16384, "output_cap": 768},
    "stress": {"input_cap": 65536, "output_cap": 1024},
}


def validate_local_endpoint(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("only local HTTP model endpoints are allowed")
    return value.rstrip("/")


def discover_local_models(endpoints: tuple[str, ...] = DEFAULT_ENDPOINTS) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for raw_endpoint in endpoints:
        endpoint = validate_local_endpoint(raw_endpoint)
        try:
            with urllib.request.urlopen(endpoint + "/models", timeout=2) as response:
                payload = json.loads(response.read())
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        for item in payload.get("data", []):
            model_id = item.get("id")
            if not isinstance(model_id, str) or not model_id:
                continue
            found.append({
                "kind": "LOCAL_AVAILABLE",
                "id": model_id,
                "label": model_id,
                "endpoint": endpoint,
                "provider": item.get("owned_by", "openai-compatible"),
                "root": item.get("root"),
                "maximum_context_tokens": item.get("max_model_len"),
                "can_run_now": True,
            })
    return found


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * pct / 100) - 1)]


def _metric(values: list[float | None]) -> dict[str, float] | None:
    clean = [float(value) for value in values if value is not None]
    if not clean:
        return None
    return {
        "median": round(statistics.median(clean), 4),
        "p95": round(_percentile(clean, 95) or 0, 4),
        "min": round(min(clean), 4),
        "max": round(max(clean), 4),
    }


def _scenario(target_tokens: int) -> tuple[list[dict[str, str]], dict[str, Any]]:
    truth = {
        "deadline": "30/11/2026",
        "guarantee_percent": 2,
        "inspection_deadline": "15/10/2026",
        "category": "OG1 III",
        "citations": ["CAP-17", "CAP-22", "CAP-31", "CAP-44"],
    }
    core = (
        "CAP-17 — Il termine ultimo per la presentazione dell'offerta è il 30/11/2026.\n"
        "CAP-22 — La garanzia provvisoria richiesta è pari al 2% dell'importo a base di gara.\n"
        "CAP-31 — Il sopralluogo è obbligatorio e deve essere completato entro il 15/10/2026.\n"
        "CAP-44 — Categoria prevalente OG1, classifica III.\n"
    )
    filler_line = "ALLEGATO-{index:04d} — Prescrizione amministrativa generale senza modifica dei requisiti CAP-17, CAP-22, CAP-31 e CAP-44.\n"
    target_chars = max(len(core), target_tokens * 4)
    parts = [core]
    index = 1
    while sum(map(len, parts)) < target_chars:
        parts.append(filler_line.format(index=index))
        index += 1
    context = "".join(parts)[:target_chars]
    messages = [
        {
            "role": "system",
            "content": "Sei un assistente per uffici gare. Usa solo il capitolato fornito e non inventare dati.",
        },
        {
            "role": "user",
            "content": (
                "CAPITOLATO:\n" + context + "\nDOMANDA:\n"
                "Estrai scadenza offerta, garanzia provvisoria, termine sopralluogo e categoria. "
                "Rispondi solo con JSON valido usando le chiavi deadline, guarantee_percent, "
                "inspection_deadline, category e citations. citations deve contenere gli ID delle clausole."
            ),
        },
    ]
    return messages, truth


def _quality(answer: str, truth: dict[str, Any]) -> dict[str, Any]:
    try:
        start, end = answer.index("{"), answer.rindex("}") + 1
        value = json.loads(answer[start:end])
    except (ValueError, json.JSONDecodeError):
        return {"score": 0.0, "pass": False, "checks": {"valid_json": False}}
    guarantee_match = re.search(r"\d+(?:[.,]\d+)?", str(value.get("guarantee_percent", "")))
    guarantee = float(guarantee_match.group(0).replace(",", ".")) if guarantee_match else None
    category = re.sub(r"[^A-Z0-9]+", " ", str(value.get("category", "")).upper()).split()
    expected_category = re.sub(r"[^A-Z0-9]+", " ", truth["category"].upper()).split()
    category_matches = all(token in category for token in expected_category)
    checks = {
        "valid_json": True,
        "deadline": value.get("deadline") == truth["deadline"],
        "guarantee_percent": guarantee == float(truth["guarantee_percent"]),
        "inspection_deadline": value.get("inspection_deadline") == truth["inspection_deadline"],
        "category": category_matches,
        "citations": set(value.get("citations", [])) >= set(truth["citations"]),
    }
    score = sum(checks.values()) / len(checks)
    return {"score": round(score, 4), "pass": all(checks.values()), "checks": checks}


def run_local_benchmark(
    *, endpoint: str, model: str, contract: dict[str, Any], profile: str,
    repetitions: int, concurrency: int,
    runner: Callable[[str, str, dict[str, Any], int], dict[str, Any]] = stream_chat,
) -> dict[str, Any]:
    endpoint = validate_local_endpoint(endpoint)
    observed_model = next((item for item in discover_local_models((endpoint,)) if item["id"] == model), None)
    if profile not in PROFILES:
        raise ValueError("unsupported benchmark profile")
    if not isinstance(repetitions, int) or not 1 <= repetitions <= 5:
        raise ValueError("repetitions must be between 1 and 5")
    if not isinstance(concurrency, int) or not 1 <= concurrency <= 8:
        raise ValueError("concurrency must be between 1 and 8")
    request = contract["request_profile"]
    source_key = {"quick": "p50", "representative": "p50", "stress": "p95"}[profile]
    target_input = min(request["input_tokens"][source_key], PROFILES[profile]["input_cap"])
    target_output = min(request["output_tokens"][source_key], PROFILES[profile]["output_cap"])
    messages, truth = _scenario(target_input)
    config = {
        "mode": "no-think",
        "messages": messages,
        "max_tokens": target_output,
        "sampling": {"temperature": 0.0, "top_p": 1.0, "top_k": -1, "presence_penalty": 0.0},
    }

    def execute(index: int) -> dict[str, Any]:
        try:
            result = runner(endpoint, model, config, 8400 + index)
            result["quality"] = _quality(result.get("answer", ""), truth)
            return result
        except Exception as exc:  # Individual failures remain visible in the measured result.
            return {"error": str(exc), "quality": {"score": 0.0, "pass": False, "checks": {}}}

    total = repetitions * concurrency
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        runs = list(pool.map(execute, range(total)))
    successful = [run for run in runs if "error" not in run]
    qualities = [run["quality"]["score"] for run in runs]
    summary = {
        "request_count": total,
        "successful_request_count": len(successful),
        "error_rate": round((total - len(successful)) / total, 4),
        "ttfa_seconds": _metric([run.get("ttfa_seconds") for run in successful]),
        "elapsed_seconds": _metric([run.get("elapsed_seconds") for run in successful]),
        "answer_tokens_per_second": _metric([run.get("answer_tokens_per_second") for run in successful]),
        "answer_tokens": _metric([run.get("answer_tokens") for run in successful]),
        "prompt_tokens": _metric([run.get("prompt_tokens") for run in successful]),
        "quality_score": _metric(qualities),
        "quality_pass_rate": round(sum(run["quality"]["pass"] for run in runs) / total, 4),
    }
    objectives = contract["service_objectives"]
    checks = {
        "ttfa_p95": {"actual": (summary["ttfa_seconds"] or {}).get("p95"), "target": objectives["ttfa_p95_ms"] / 1000},
        "end_to_end_p95": {"actual": (summary["elapsed_seconds"] or {}).get("p95"), "target": objectives["end_to_end_p95_ms"] / 1000},
        "answer_speed_min": {"actual": (summary["answer_tokens_per_second"] or {}).get("min"), "target": objectives["answer_tokens_per_second_min"]},
        "error_rate": {"actual": summary["error_rate"], "target": objectives["error_rate_max"]},
        "quality_minimum": {"actual": (summary["quality_score"] or {}).get("min"), "target": contract["quality"]["minimum_score"]},
    }
    for name, check in checks.items():
        actual = check["actual"]
        check["pass"] = actual is not None and (actual >= check["target"] if name in {"answer_speed_min", "quality_minimum"} else actual <= check["target"])
    observed_min_tps = (summary["answer_tokens_per_second"] or {}).get("min")
    contract_p95_output_tokens = request["output_tokens"]["p95"]
    generation_seconds_at_observed_rate = (
        round(contract_p95_output_tokens / observed_min_tps, 2)
        if observed_min_tps and observed_min_tps > 0 else None
    )
    return {
        "schema": "srh.local-readiness-benchmark.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provenance": "LOCAL_OBSERVED_READINESS_TEST",
        "endpoint": endpoint,
        "model": model,
        "runtime_root": observed_model.get("root") if observed_model else None,
        "maximum_context_tokens": observed_model.get("maximum_context_tokens") if observed_model else None,
        "profile": profile,
        "requested_input_tokens": target_input,
        "requested_output_tokens": target_output,
        "contract_p95_input_tokens": request["input_tokens"]["p95"],
        "contract_p95_output_tokens": contract_p95_output_tokens,
        "repetitions": repetitions,
        "concurrency": concurrency,
        "scenario": "synthetic-italian-construction-tender-v1",
        "summary": summary,
        "checks": checks,
        "objective_summary": {
            "met": sum(check["pass"] for check in checks.values()),
            "total": len(checks),
        },
        "planning_bridge": {
            "generation_seconds_for_contract_p95_output_at_observed_min_rate": generation_seconds_at_observed_rate,
            "explanation": "This extrapolates generation only. It excludes p95 prompt processing and is not a replacement for the stress benchmark.",
        },
        "all_checks_pass": all(check["pass"] for check in checks.values()),
        "runs": runs,
        "decision_boundary": "Readiness evidence for this synthetic scenario; client documents and expert ground truth are still required.",
    }
