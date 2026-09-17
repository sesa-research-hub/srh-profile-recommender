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
import subprocess
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlparse
import urllib.request

from srh.benchmark import stream_chat


DEFAULT_ENDPOINTS = ("http://127.0.0.1:18300/v1", "http://127.0.0.1:8000/v1")
OLLAMA_API = "http://127.0.0.1:11434"
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


def _read_json(url: str, timeout: int = 2) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        value = json.loads(response.read())
    if not isinstance(value, dict):
        raise ValueError("model inventory must be a JSON object")
    return value


def _is_embedding_model(value: dict[str, Any]) -> bool:
    details = value.get("details") or {}
    text = " ".join(str(item).lower() for item in (
        value.get("name"), value.get("model"), details.get("family"),
        *(details.get("families") or []),
    ) if item)
    return any(marker in text for marker in ("embed", "nomic-bert", "bge-", "e5-"))


def discover_ollama_models(api_base: str = OLLAMA_API) -> list[dict[str, Any]]:
    """Return installed generative Ollama models, including immutable identity metadata."""
    api_base = validate_local_endpoint(api_base)
    try:
        payload = _read_json(api_base + "/api/tags")
    except (OSError, ValueError, json.JSONDecodeError):
        return []
    try:
        running = _read_json(api_base + "/api/ps")
    except (OSError, ValueError, json.JSONDecodeError):
        running = {"models": []}
    loaded = {str(item.get("digest")) for item in running.get("models", []) if item.get("digest")}
    found: list[dict[str, Any]] = []
    for item in payload.get("models", []):
        if not isinstance(item, dict) or _is_embedding_model(item):
            continue
        model_id = item.get("name") or item.get("model")
        digest = item.get("digest")
        if not isinstance(model_id, str) or not model_id or not isinstance(digest, str) or not digest:
            continue
        details = item.get("details") or {}
        is_loaded = digest in loaded
        found.append({
            "kind": "LOCAL_AVAILABLE",
            "id": model_id,
            "label": model_id,
            "endpoint": api_base + "/v1",
            "provider": "ollama",
            "root": f"ollama://{model_id}@sha256:{digest}",
            "runtime_fingerprint": f"ollama:{digest}",
            "digest": digest,
            "installed_size_bytes": item.get("size"),
            "family": details.get("family"),
            "parameter_size": details.get("parameter_size"),
            "quantization": details.get("quantization_level"),
            "maximum_context_tokens": None,
            "loaded": is_loaded,
            "availability": "LOADED" if is_loaded else "INSTALLED_LOAD_ON_DEMAND",
            "can_run_now": True,
        })
    return found


def _ollama_show(model: str, api_base: str = OLLAMA_API) -> dict[str, Any]:
    request = urllib.request.Request(
        validate_local_endpoint(api_base) + "/api/show",
        data=json.dumps({"model": model}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            value = json.loads(response.read())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def _model_parameters(raw: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    if not isinstance(raw, str):
        return result
    for line in raw.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2:
            result[parts[0]] = parts[1]
    return result


def inspect_runtime_configuration(endpoint: str, model: str, observed: dict[str, Any] | None) -> dict[str, Any]:
    provider = (observed or {}).get("provider", "openai-compatible")
    result: dict[str, Any] = {
        "provider": provider,
        "model": model,
        "runtime_identity": (observed or {}).get("runtime_fingerprint"),
        "quantization": (observed or {}).get("quantization"),
        "family": (observed or {}).get("family"),
        "parameter_size": (observed or {}).get("parameter_size"),
        "maximum_context_tokens": (observed or {}).get("maximum_context_tokens"),
        "kv_cache": {"policy": "runtime_managed", "compression": (observed or {}).get("kv_cache_dtype") or "not_exposed_by_runtime_api"},
        "speculative_decoding": {"status": "enabled" if (observed or {}).get("speculative_config") else "not_exposed_by_runtime_api", "configuration": (observed or {}).get("speculative_config")},
    }
    if endpoint == OLLAMA_API + "/v1":
        shown = _ollama_show(model)
        info = shown.get("model_info") or {}
        context = next((value for key, value in info.items() if key.endswith(".context_length")), None)
        result.update({
            "provider": "ollama",
            "architecture": info.get("general.architecture"),
            "parameter_count": info.get("general.parameter_count"),
            "maximum_context_tokens": context,
            "model_defaults": _model_parameters(shown.get("parameters")),
            "capabilities": shown.get("capabilities") or [],
            "ollama_versioned_digest": (observed or {}).get("digest"),
        })
    return result


def _gpu_power_sample() -> tuple[str, float] | None:
    try:
        output = subprocess.check_output([
            "nvidia-smi", "--query-gpu=name,power.draw", "--format=csv,noheader,nounits",
        ], text=True, timeout=3).strip().splitlines()[0]
        name, raw_power = output.rsplit(",", 1)
        return name.strip(), float(raw_power.strip())
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


def _power_measurement(work: Callable[[], list[dict[str, Any]]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    baseline_values = [sample[1] for sample in (_gpu_power_sample() for _ in range(3)) if sample]
    samples: list[float] = []
    gpu_name = None
    stop = threading.Event()

    def monitor() -> None:
        nonlocal gpu_name
        while not stop.is_set():
            sample = _gpu_power_sample()
            if sample:
                gpu_name, value = sample
                samples.append(value)
            stop.wait(0.25)

    started = time.perf_counter()
    thread = threading.Thread(target=monitor, daemon=True)
    thread.start()
    try:
        result = work()
    finally:
        stop.set()
        thread.join(timeout=4)
    duration = time.perf_counter() - started
    baseline = statistics.mean(baseline_values) if baseline_values else None
    average = statistics.mean(samples) if samples else None
    incremental = max(0.0, average - baseline) if average is not None and baseline is not None else None
    return result, {
        "supported": bool(samples),
        "gpu_name": gpu_name,
        "sample_count": len(samples),
        "baseline_power_w": round(baseline, 2) if baseline is not None else None,
        "average_power_w": round(average, 2) if average is not None else None,
        "peak_power_w": round(max(samples), 2) if samples else None,
        "incremental_average_power_w": round(incremental, 2) if incremental is not None else None,
        "campaign_duration_seconds": round(duration, 3),
        "energy_wh": round(average * duration / 3600, 4) if average is not None else None,
        "incremental_energy_wh": round(incremental * duration / 3600, 4) if incremental is not None else None,
        "method": "nvidia-smi power.draw sampled during the complete benchmark",
        "boundary": "Host GPU observation; other concurrent GPU activity can affect the measurement.",
    }


def discover_local_models(
    endpoints: tuple[str, ...] | None = None, *, include_ollama: bool | None = None,
) -> list[dict[str, Any]]:
    if endpoints is None:
        endpoints = DEFAULT_ENDPOINTS
        include_ollama = True if include_ollama is None else include_ollama
    elif include_ollama is None:
        include_ollama = False
    found: list[dict[str, Any]] = []
    for raw_endpoint in endpoints:
        endpoint = validate_local_endpoint(raw_endpoint)
        try:
            payload = _read_json(endpoint + "/models")
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        for item in payload.get("data", []):
            model_id = item.get("id")
            if not isinstance(model_id, str) or not model_id:
                continue
            root = item.get("root")
            found.append({
                "kind": "LOCAL_AVAILABLE",
                "id": model_id,
                "label": model_id,
                "endpoint": endpoint,
                "provider": item.get("owned_by", "openai-compatible"),
                "root": root,
                "runtime_fingerprint": str(root or f"{endpoint}:{model_id}"),
                "quantization": item.get("quantization"),
                "family": item.get("family"),
                "parameter_size": item.get("parameter_size"),
                "kv_cache_dtype": item.get("kv_cache_dtype") or item.get("cache_dtype"),
                "speculative_config": item.get("speculative_config"),
                "maximum_context_tokens": item.get("max_model_len"),
                "loaded": True,
                "availability": "LOADED",
                "can_run_now": True,
            })
    if include_ollama:
        found.extend(discover_ollama_models())
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
    discovered = discover_ollama_models() if endpoint == OLLAMA_API + "/v1" else discover_local_models((endpoint,))
    observed_model = next((item for item in discovered if item["id"] == model), None)
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
    warmup = execute(-1)

    def measured_work() -> list[dict[str, Any]]:
        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
            return list(pool.map(execute, range(total)))

    runs, energy = _power_measurement(measured_work)
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
        "runtime_configuration": {
            **inspect_runtime_configuration(endpoint, model, observed_model),
            "effective_test_protocol": {
                "reasoning": "disabled",
                "temperature": config["sampling"]["temperature"],
                "top_p": config["sampling"]["top_p"],
                "top_k": config["sampling"]["top_k"],
                "presence_penalty": config["sampling"]["presence_penalty"],
                "maximum_output_tokens": target_output,
                "warmup_requests_excluded": 1,
            },
        },
        "energy_observation": energy,
        "warmup": {
            "success": "error" not in warmup,
            "elapsed_seconds": warmup.get("elapsed_seconds"),
            "error": warmup.get("error"),
            "included_in_kpis": False,
        },
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
