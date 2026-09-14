#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
#
# Copyright 2026 Sesa Research Hub
#
# SRH Private AI - Workload Benchmark Runner

from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


SRH_VERSION = "0.3.1"


SCENARIOS = {
    "chat": {
        "description": "Short enterprise assistant interaction",
        "mode": "no-think",
        "messages": [
            {
                "role": "user",
                "content": (
                    "Spiega a un responsabile di una PMI la differenza tra "
                    "backup e disaster recovery. Sii concreto e resta entro "
                    "100 parole."
                ),
            }
        ],
        "max_tokens": 256,
        "sampling": {
            "temperature": 0.7,
            "top_p": 0.8,
            "top_k": 20,
            "presence_penalty": 1.5,
        },
    },

    "rag": {
        "description": "Grounded enterprise-style question",
        "mode": "no-think",
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
        "sampling": {
            "temperature": 0.7,
            "top_p": 0.8,
            "top_k": 20,
            "presence_penalty": 1.5,
        },
    },

    "reasoning": {
        "description": "Structured operational reasoning",
        "mode": "think",
        "messages": [
            {
                "role": "user",
                "content": (
                    "Una PMI ha tre linee produttive. La linea A produce "
                    "120 pezzi/ora con 5% di scarto, B 90 pezzi/ora con 2% "
                    "di scarto, C 150 pezzi/ora con 8% di scarto. "
                    "Calcola la produzione buona oraria complessiva e indica "
                    "quale linea produce più pezzi buoni. Mostra brevemente "
                    "i calcoli."
                ),
            }
        ],
        "max_tokens": 1024,
        "sampling": {
            "temperature": 1.0,
            "top_p": 0.95,
            "top_k": 20,
            "presence_penalty": 0.0,
        },
    },
}


def fetch_model_info(base_url: str, model: str) -> dict | None:
    try:
        with urllib.request.urlopen(
            f"{base_url.rstrip('/')}/models",
            timeout=15,
        ) as response:
            data = json.loads(response.read())

        for item in data.get("data", []):
            if item.get("id") == model:
                return item

    except Exception:
        pass

    return None


def stream_chat(
    base_url: str,
    model: str,
    config: dict,
) -> dict:

    think_mode = config["mode"] == "think"

    payload = {
        "model": model,
        "messages": config["messages"],
        "max_tokens": config["max_tokens"],
        "stream": True,
        "stream_options": {
            "include_usage": True,
        },
        "chat_template_kwargs": {
            "enable_thinking": think_mode,
        },
        **config["sampling"],
    }

    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
        method="POST",
    )

    request_start = time.perf_counter()

    first_generated_time = None
    first_answer_time = None
    end_time = None

    answer_parts = []

    reasoning_chars = 0
    answer_chars = 0

    usage = {}
    finish_reason = None
    chunks_received = 0
    reasoning_chunks = 0
    answer_chunks = 0

    with urllib.request.urlopen(req, timeout=300) as response:
        for raw_line in response:

            line = raw_line.decode("utf-8").strip()

            if not line or not line.startswith("data:"):
                continue

            data = line[5:].strip()

            if data == "[DONE]":
                end_time = time.perf_counter()
                break

            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue

            chunks_received += 1

            if chunk.get("usage"):
                usage = chunk["usage"]

            choices = chunk.get("choices") or []

            for choice in choices:

                if choice.get("finish_reason") is not None:
                    finish_reason = choice["finish_reason"]

                delta = choice.get("delta") or {}

                reasoning = (
                    delta.get("reasoning")
                    or delta.get("reasoning_content")
                )

                content = delta.get("content")

                if reasoning:

                    now = time.perf_counter()

                    if first_generated_time is None:
                        first_generated_time = now

                    reasoning_chunks += 1
                    reasoning_chars += len(reasoning)

                if content:

                    now = time.perf_counter()

                    if first_generated_time is None:
                        first_generated_time = now

                    if first_answer_time is None:
                        first_answer_time = now

                    answer_chunks += 1
                    answer_chars += len(content)
                    answer_parts.append(content)

    if end_time is None:
        end_time = time.perf_counter()

    e2e = end_time - request_start

    ttft = None
    ttfa = None
    generation_seconds = None
    answer_generation_seconds = None
    reasoning_phase_seconds = None

    if first_generated_time is not None:
        ttft = first_generated_time - request_start
        generation_seconds = end_time - first_generated_time

    if first_answer_time is not None:
        ttfa = first_answer_time - request_start
        answer_generation_seconds = end_time - first_answer_time

    if (
        first_generated_time is not None
        and first_answer_time is not None
        and first_answer_time >= first_generated_time
    ):
        reasoning_phase_seconds = (
            first_answer_time - first_generated_time
        )

    prompt_tokens = usage.get("prompt_tokens")
    completion_tokens = usage.get("completion_tokens")
    total_tokens = usage.get("total_tokens")

    completion_details = (
        usage.get("completion_tokens_details") or {}
    )

    reasoning_tokens = completion_details.get("reasoning_tokens")

    answer_tokens = None

    if completion_tokens is not None:
        if reasoning_tokens is not None:
            answer_tokens = max(
                completion_tokens - reasoning_tokens,
                0,
            )
        elif not think_mode:
            answer_tokens = completion_tokens

    total_generation_tps = None

    if (
        completion_tokens is not None
        and completion_tokens > 1
        and generation_seconds
        and generation_seconds > 0
    ):
        total_generation_tps = (
            (completion_tokens - 1) / generation_seconds
        )

    reasoning_tps = None

    if (
        reasoning_tokens is not None
        and reasoning_tokens > 1
        and reasoning_phase_seconds
        and reasoning_phase_seconds > 0
    ):
        reasoning_tps = (
            (reasoning_tokens - 1) / reasoning_phase_seconds
        )

    answer_tps = None

    if (
        answer_tokens is not None
        and answer_tokens > 1
        and answer_generation_seconds
        and answer_generation_seconds > 0
    ):
        answer_tps = (
            (answer_tokens - 1) / answer_generation_seconds
        )

    e2e_completion_tps = None

    if completion_tokens and e2e > 0:
        e2e_completion_tps = completion_tokens / e2e

    return {
        "mode": config["mode"],

        "elapsed_seconds": round(e2e, 4),

        "ttft_seconds": (
            round(ttft, 4)
            if ttft is not None
            else None
        ),

        "ttfa_seconds": (
            round(ttfa, 4)
            if ttfa is not None
            else None
        ),

        "reasoning_phase_seconds": (
            round(reasoning_phase_seconds, 4)
            if reasoning_phase_seconds is not None
            else None
        ),

        "generation_seconds": (
            round(generation_seconds, 4)
            if generation_seconds is not None
            else None
        ),

        "answer_generation_seconds": (
            round(answer_generation_seconds, 4)
            if answer_generation_seconds is not None
            else None
        ),

        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "reasoning_tokens": reasoning_tokens,
        "answer_tokens": answer_tokens,
        "total_tokens": total_tokens,

        "total_generation_tokens_per_second": (
            round(total_generation_tps, 2)
            if total_generation_tps is not None
            else None
        ),

        "reasoning_tokens_per_second": (
            round(reasoning_tps, 2)
            if reasoning_tps is not None
            else None
        ),

        "answer_tokens_per_second": (
            round(answer_tps, 2)
            if answer_tps is not None
            else None
        ),

        "end_to_end_completion_tps": (
            round(e2e_completion_tps, 2)
            if e2e_completion_tps is not None
            else None
        ),

        "reasoning_chunks": reasoning_chunks,
        "answer_chunks": answer_chunks,

        "reasoning_chars": reasoning_chars,
        "answer_chars": answer_chars,

        "chunks_received": chunks_received,

        "finish_reason": finish_reason,

        "answer": "".join(answer_parts),
    }


def summarize_runs(runs: list[dict]) -> dict:

    successful = [
        r for r in runs
        if "error" not in r
    ]

    fields = [
        "elapsed_seconds",
        "ttft_seconds",
        "ttfa_seconds",
        "reasoning_phase_seconds",
        "generation_seconds",
        "answer_generation_seconds",
        "total_generation_tokens_per_second",
        "reasoning_tokens_per_second",
        "answer_tokens_per_second",
        "end_to_end_completion_tps",
    ]

    summary = {}

    for field in fields:

        vals = [
            r[field]
            for r in successful
            if r.get(field) is not None
        ]

        if vals:
            summary[field] = {
                "median": round(
                    statistics.median(vals),
                    4,
                ),
                "min": round(min(vals), 4),
                "max": round(max(vals), 4),
            }

    return summary


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

    parser.add_argument(
        "--runs",
        type=int,
        default=1,
    )

    args = parser.parse_args()

    if args.runs < 1:
        parser.error("--runs must be >= 1")

    selected = (
        SCENARIOS
        if args.scenario == "all"
        else {args.scenario: SCENARIOS[args.scenario]}
    )

    model_info = fetch_model_info(
        args.base_url,
        args.model,
    )

    print()
    print("SRH PRIVATE AI - WORKLOAD BENCHMARK")
    print("=" * 72)

    print(f"SRH version : {SRH_VERSION}")
    print(f"Endpoint    : {args.base_url}")
    print(f"Model       : {args.model}")

    if model_info:
        print(f"Backend     : {model_info.get('owned_by')}")
        print(f"Context     : {model_info.get('max_model_len')}")
        print(f"Snapshot    : {model_info.get('root')}")

    print(f"Runs        : {args.runs}")
    print()

    scenario_results = []

    for name, config in selected.items():

        print(
            f"[{name}] "
            f"{config['description']} "
            f"| mode={config['mode']}"
        )

        runs = []

        for run_no in range(
            1,
            args.runs + 1,
        ):

            print(
                f"  run {run_no}/{args.runs}...",
                end=" ",
                flush=True,
            )

            try:

                result = stream_chat(
                    args.base_url,
                    args.model,
                    config,
                )

                runs.append(result)

                print(
                    f"TTFT={result['ttft_seconds']}s | "
                    f"TTFA={result['ttfa_seconds']}s | "
                    f"gen={result['generation_seconds']}s | "
                    f"tok={result['completion_tokens']} | "
                    f"genTPS="
                    f"{result['total_generation_tokens_per_second']} | "
                    f"finish={result['finish_reason']}"
                )

            except Exception as exc:

                print(f"ERROR: {exc}")

                runs.append({
                    "error": str(exc),
                })

        scenario_results.append({
            "scenario": name,
            "description": config["description"],
            "mode": config["mode"],
            "runs": runs,
            "summary": summarize_runs(runs),
        })

        print()

    report = {
        "schema": "srh.workload-benchmark.v3",
        "srh_version": SRH_VERSION,
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "endpoint": args.base_url,
        "model": args.model,
        "model_info": model_info,
        "runs_per_scenario": args.runs,
        "results": scenario_results,
    }

    root = Path(__file__).resolve().parent.parent

    out_dir = (
        root
        / "srh"
        / "benchmarks"
        / "results"
    )

    out_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    timestamp = datetime.now().strftime(
        "%Y%m%d-%H%M%S"
    )

    output = (
        out_dir
        / f"benchmark-{timestamp}.json"
    )

    output.write_text(
        json.dumps(
            report,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print(
        f"Report written: {output}"
    )

    print()


if __name__ == "__main__":
    main()
