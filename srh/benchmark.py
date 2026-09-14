#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
#
# Copyright 2026 Sesa Research Hub
#
# SRH Private AI - Workload Benchmark Runner

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


SRH_VERSION = "0.1.0"


SCENARIOS = {
    "chat": {
        "description": "Short enterprise assistant interaction",
        "messages": [
            {
                "role": "user",
                "content": (
                    "Spiega in modo sintetico a un responsabile di una PMI "
                    "la differenza tra backup e disaster recovery."
                ),
            }
        ],
        "max_tokens": 256,
    },

    "rag": {
        "description": "Grounded enterprise-style question",
        "messages": [
            {
                "role": "system",
                "content": (
                    "Rispondi esclusivamente usando il contesto fornito. "
                    "Se il dato non è presente, dichiaralo."
                ),
            },
            {
                "role": "user",
                "content": (
                    "CONTESTO:\n"
                    "Ordine A102: cliente Alfa, consegna prevista 18 settembre, "
                    "stato produzione completato, collaudo ancora da eseguire.\n"
                    "Ordine B205: cliente Beta, consegna prevista 22 settembre, "
                    "stato produzione in corso.\n\n"
                    "DOMANDA: quale ordine è più vicino alla consegna e quale "
                    "attività risulta ancora necessaria?"
                ),
            },
        ],
        "max_tokens": 256,
    },

    "reasoning": {
        "description": "Structured operational reasoning",
        "messages": [
            {
                "role": "user",
                "content": (
                    "Una PMI ha tre linee produttive. La linea A produce "
                    "120 pezzi/ora con 5% di scarto, B 90 pezzi/ora con 2% "
                    "di scarto, C 150 pezzi/ora con 8% di scarto. "
                    "Calcola la produzione buona oraria complessiva e indica "
                    "quale linea produce più pezzi buoni."
                ),
            }
        ],
        "max_tokens": 384,
    },
}


def post_json(url: str, payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    start = time.perf_counter()

    with urllib.request.urlopen(req, timeout=300) as response:
        raw = response.read()

    elapsed = time.perf_counter() - start

    return json.loads(raw), elapsed


def run_scenario(
    base_url: str,
    model: str,
    name: str,
    config: dict,
) -> dict:

    payload = {
        "model": model,
        "messages": config["messages"],
        "temperature": 0,
        "max_tokens": config["max_tokens"],
    }

    response, elapsed = post_json(
        f"{base_url.rstrip('/')}/chat/completions",
        payload,
    )

    usage = response.get("usage", {})

    prompt_tokens = usage.get("prompt_tokens")
    completion_tokens = usage.get("completion_tokens")
    total_tokens = usage.get("total_tokens")

    completion_tps = None

    if completion_tokens and elapsed > 0:
        completion_tps = round(completion_tokens / elapsed, 2)

    choices = response.get("choices", [])

    answer = None
    finish_reason = None

    if choices:
        answer = choices[0].get("message", {}).get("content")
        finish_reason = choices[0].get("finish_reason")

    return {
        "scenario": name,
        "description": config["description"],
        "elapsed_seconds": round(elapsed, 4),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "end_to_end_completion_tps": completion_tps,
        "finish_reason": finish_reason,
        "answer": answer,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="SRH Private AI workload benchmark runner"
    )

    parser.add_argument(
        "--base-url",
        default="http://localhost:18300/v1",
    )

    parser.add_argument(
        "--model",
        default="qwen3.8-flash-next",
    )

    parser.add_argument(
        "--scenario",
        choices=["all", *SCENARIOS.keys()],
        default="all",
    )

    args = parser.parse_args()

    selected = (
        SCENARIOS
        if args.scenario == "all"
        else {args.scenario: SCENARIOS[args.scenario]}
    )

    print()
    print("SRH PRIVATE AI - WORKLOAD BENCHMARK")
    print("=" * 60)
    print(f"Endpoint : {args.base_url}")
    print(f"Model    : {args.model}")
    print()

    results = []

    for name, config in selected.items():
        print(f"Running {name}...", end=" ", flush=True)

        try:
            result = run_scenario(
                args.base_url,
                args.model,
                name,
                config,
            )

            results.append(result)

            print(
                f"{result['elapsed_seconds']}s | "
                f"{result['completion_tokens']} tokens | "
                f"{result['end_to_end_completion_tps']} tok/s"
            )

        except Exception as exc:
            print(f"ERROR: {exc}")

            results.append(
                {
                    "scenario": name,
                    "error": str(exc),
                }
            )

    report = {
        "schema": "srh.workload-benchmark.v1",
        "srh_version": SRH_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "endpoint": args.base_url,
        "model": args.model,
        "results": results,
    }

    root = Path(__file__).resolve().parent.parent

    out_dir = root / "srh" / "benchmarks" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    output = out_dir / f"benchmark-{timestamp}.json"

    output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print()
    print(f"Report written: {output}")
    print()


if __name__ == "__main__":
    main()
