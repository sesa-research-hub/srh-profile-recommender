#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
#
# Copyright 2026 Sesa Research Hub
#
# SRH Private AI - Synthetic Enterprise Scenario Generator

from __future__ import annotations

import argparse
import hashlib
import json
import random
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


GENERATOR_VERSION = "0.3.0"
PACK_SCHEMA = "srh.synthetic-scenario-pack.v1"
SCENARIO_ID = "enterprise-orders-rag-v3"

TARGET_SOURCE = "SRC-TARGET-001"

TARGET_RECORD = (
    f"[{TARGET_SOURCE}] "
    "ordine=A102 | cliente=Alfa | consegna=2026-09-18 | "
    "stato=produzione completata | "
    "attivita_residua=collaudo finale da eseguire | "
    "reparto=assemblaggio | priorita=alta"
)

HARD_NEGATIVES = [
    (
        "[SRC-HARD-001] "
        "ordine=A103 | cliente=Alfa | consegna=2026-09-19 | "
        "stato=produzione completata | "
        "attivita_residua=collaudo finale da eseguire | "
        "reparto=assemblaggio | priorita=alta"
    ),
    (
        "[SRC-HARD-002] "
        "ordine=B205 | cliente=Beta | consegna=2026-09-18 | "
        "stato=produzione completata | "
        "attivita_residua=collaudo finale da eseguire | "
        "reparto=assemblaggio | priorita=alta"
    ),
    (
        "[SRC-HARD-003] "
        "ordine=A099 | cliente=Alfa | consegna=2026-09-17 | "
        "stato=collaudo completato | "
        "attivita_residua=imballaggio | "
        "reparto=logistica | priorita=alta"
    ),
]


CLIENTS = [
    "Alfa", "Beta", "Gamma", "Delta", "Epsilon",
    "Futura", "Leonardo", "Tirreno", "Aurora", "Valdarno",
]

STATUSES = [
    "produzione in corso",
    "produzione completata",
    "in attesa materiali",
    "collaudo in corso",
    "pronto per spedizione",
]

ACTIVITIES = [
    "controllo qualita",
    "imballaggio",
    "approvazione documentale",
    "preparazione spedizione",
    "collaudo finale",
    "verifica dimensionale",
    "nessuna",
]

DEPARTMENTS = [
    "assemblaggio",
    "produzione",
    "qualita",
    "logistica",
    "lavorazioni esterne",
]

PRIORITIES = ["bassa", "normale", "alta"]


def tokenize(
    server_url: str,
    model: str,
    text: str,
) -> int:
    payload = {
        "model": model,
        "prompt": text,
        "add_special_tokens": False,
    }

    request = urllib.request.Request(
        f"{server_url.rstrip('/')}/tokenize",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=120) as response:
        result = json.loads(response.read())

    count = result.get("count")

    if not isinstance(count, int):
        raise RuntimeError(
            f"tokenizer returned invalid count: {result!r}"
        )

    return count


def make_filler_records(
    count: int,
    seed: int,
) -> list[str]:
    rng = random.Random(seed)
    records: list[str] = []

    base_date = date(2026, 9, 1)

    for index in range(1, count + 1):
        client = rng.choice(CLIENTS)
        delivery = base_date + timedelta(
            days=rng.randint(0, 89)
        )

        # Preserve uniqueness of the target query.
        if (
            client == "Alfa"
            and delivery == date(2026, 9, 18)
        ):
            delivery += timedelta(days=1)

        status = rng.choice(STATUSES)
        activity = rng.choice(ACTIVITIES)
        department = rng.choice(DEPARTMENTS)
        priority = rng.choice(PRIORITIES)

        records.append(
            f"[SRC-{index:06d}] "
            f"ordine=O{100000 + index} | "
            f"cliente={client} | "
            f"consegna={delivery.isoformat()} | "
            f"stato={status} | "
            f"attivita_residua={activity} | "
            f"reparto={department} | "
            f"priorita={priority}"
        )

    return records


def build_context(
    filler_count: int,
    seed: int,
) -> tuple[str, int, int]:
    records = make_filler_records(
        filler_count,
        seed,
    )

    # Add deliberately confusing but non-conflicting records.
    if records:
        first = max(0, len(records) // 5)
        second = max(0, len(records) // 2)

        records.insert(first, HARD_NEGATIVES[0])
        records.insert(second, HARD_NEGATIVES[1])
        records.append(HARD_NEGATIVES[2])
    else:
        records.extend(HARD_NEGATIVES)

    # Keep the answer away from the beginning/end of the context.
    target_index = int(len(records) * 0.62)
    records.insert(target_index, TARGET_RECORD)

    header = (
        "ARCHIVIO ORDINI E PRODUZIONE\n"
        "I record seguenti provengono dal gestionale aziendale. "
        "Ogni record possiede un identificativo fonte SRC.\n\n"
    )

    context = header + "\n".join(records)

    return context, target_index, len(records)


def render_request(context: str) -> str:
    return (
        "Sei un assistente aziendale che interroga dati gestionali.\n"
        "Usa esclusivamente le informazioni presenti nel contesto.\n"
        "Non inferire dati mancanti e non confondere record simili.\n"
        "Nella risposta indica sempre l'identificativo SRC della fonte.\n\n"
        "CONTESTO\n"
        "========\n"
        f"{context}\n\n"
        "DOMANDA\n"
        "=======\n"
        "Per il cliente Alfa, quale ordine ha consegna prevista "
        "il 18 settembre 2026 e quale attivita risulta ancora "
        "necessaria? Indica anche l'identificativo SRC che prova "
        "la risposta.\n\n"
        "Emetti come prima riga un risultato machine-readable "
        "nel formato seguente, senza markdown:\n"
        "SRH_RESULT: {\"order\":\"<ordine>\","
        "\"activity\":\"<attivita>\","
        "\"evidence_source_id\":\"<SRC>\"}\n"
        "Dopo questa prima riga puoi motivare brevemente la risposta "
        "e discutere eventuali record simili. Non emettere un secondo "
        "SRH_RESULT.\n"
    )


def materialize(
    filler_count: int,
    seed: int,
) -> tuple[str, str, int, int]:
    context, target_index, total_records = build_context(
        filler_count,
        seed,
    )

    request = render_request(context)

    return (
        context,
        request,
        target_index,
        total_records,
    )


def calibrate(
    server_url: str,
    model: str,
    target_tokens: int,
    seed: int,
) -> dict[str, Any]:
    cache: dict[int, dict[str, Any]] = {}

    def measure(filler_count: int) -> dict[str, Any]:
        if filler_count in cache:
            return cache[filler_count]

        (
            context,
            request,
            target_index,
            total_records,
        ) = materialize(
            filler_count,
            seed,
        )

        token_count = tokenize(
            server_url,
            model,
            request,
        )

        result = {
            "filler_count": filler_count,
            "context": context,
            "request": request,
            "token_count": token_count,
            "target_index": target_index,
            "total_records": total_records,
        }

        cache[filler_count] = result
        return result

    low = 0
    high = 128

    while measure(high)["token_count"] < target_tokens:
        low = high
        high *= 2

        if high > 100000:
            raise RuntimeError(
                "unable to reach requested token target"
            )

    best = min(
        (measure(low), measure(high)),
        key=lambda item: abs(
            item["token_count"] - target_tokens
        ),
    )

    while low <= high:
        middle = (low + high) // 2
        result = measure(middle)

        if abs(
            result["token_count"] - target_tokens
        ) < abs(
            best["token_count"] - target_tokens
        ):
            best = result

        if result["token_count"] < target_tokens:
            low = middle + 1
        elif result["token_count"] > target_tokens:
            high = middle - 1
        else:
            best = result
            break

    # Inspect the nearest remaining candidates as well.
    for candidate in range(
        max(0, high - 2),
        low + 3,
    ):
        result = measure(candidate)

        if abs(
            result["token_count"] - target_tokens
        ) < abs(
            best["token_count"] - target_tokens
        ):
            best = result

    return best


def sha256_text(text: str) -> str:
    return hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a token-calibrated SRH synthetic "
            "enterprise RAG scenario"
        )
    )

    parser.add_argument(
        "--server-url",
        default="http://localhost:18300",
    )

    parser.add_argument(
        "--model",
        default="qwen3.8-flash-next",
    )

    parser.add_argument(
        "--target-tokens",
        type=int,
        required=True,
    )

    parser.add_argument(
        "--seed",
        type=int,
        default=42,
    )

    parser.add_argument(
        "--output-dir",
    )

    args = parser.parse_args()

    if args.target_tokens < 1000:
        parser.error("--target-tokens must be >= 1000")

    result = calibrate(
        args.server_url,
        args.model,
        args.target_tokens,
        args.seed,
    )

    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        output_dir = (
            Path(__file__).resolve().parent
            / "generated"
            / SCENARIO_ID
            / str(args.target_tokens)
        )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    context = result["context"]
    request = result["request"]
    actual_tokens = result["token_count"]

    context_path = output_dir / "context.txt"
    request_path = output_dir / "request.txt"
    truth_path = output_dir / "ground_truth.json"
    manifest_path = output_dir / "manifest.json"

    context_path.write_text(
        context,
        encoding="utf-8",
    )

    request_path.write_text(
        request,
        encoding="utf-8",
    )

    ground_truth = {
        "schema": "srh.scenario-ground-truth.v1",
        "scenario_id": SCENARIO_ID,
        "question": (
            "Per il cliente Alfa, quale ordine ha consegna "
            "prevista il 18 settembre 2026 e quale attivita "
            "risulta ancora necessaria?"
        ),
        "expected": {
            "order": "A102",
            "activity": "collaudo finale da eseguire",
            "evidence_source_id": TARGET_SOURCE,
        },
        "evaluation": {
            "mode": "structured-final",
            "marker": "SRH_RESULT:"
        },
        "forbidden_selections": [
            "A103",
            "B205",
            "SRC-HARD-001",
            "SRC-HARD-002",
            "SRC-HARD-003"
        ],
    }

    truth_path.write_text(
        json.dumps(
            ground_truth,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    manifest = {
        "schema": PACK_SCHEMA,
        "generator_version": GENERATOR_VERSION,
        "scenario_id": SCENARIO_ID,
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "seed": args.seed,
        "model": args.model,
        "tokenizer_endpoint": (
            f"{args.server_url.rstrip('/')}/tokenize"
        ),
        "calibration": {
            "measurement": "rendered-request-content",
            "target_tokens": args.target_tokens,
            "actual_tokens": actual_tokens,
            "delta_tokens": (
                actual_tokens - args.target_tokens
            ),
            "relative_error": round(
                abs(
                    actual_tokens - args.target_tokens
                ) / args.target_tokens,
                6,
            ),
        },
        "corpus": {
            "filler_records": result["filler_count"],
            "total_records": result["total_records"],
            "target_record_index": result["target_index"],
            "target_position_ratio": round(
                result["target_index"]
                / result["total_records"],
                4,
            ),
        },
        "ground_truth": {
            "order": "A102",
            "activity": "collaudo finale da eseguire",
            "evidence_source_id": TARGET_SOURCE,
        },
        "integrity": {
            "context_sha256": sha256_text(context),
            "request_sha256": sha256_text(request),
        },
    }

    manifest_path.write_text(
        json.dumps(
            manifest,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print("SRH SYNTHETIC ENTERPRISE SCENARIO")
    print("=" * 64)
    print(f"Generator      : {GENERATOR_VERSION}")
    print(f"Scenario       : {SCENARIO_ID}")
    print(f"Model tokenizer: {args.model}")
    print(f"Target tokens  : {args.target_tokens}")
    print(f"Actual tokens  : {actual_tokens}")
    print(
        "Delta          : "
        f"{actual_tokens - args.target_tokens:+d}"
    )
    print(f"Records        : {result['total_records']}")
    print(
        "Target position: "
        f"{manifest['corpus']['target_position_ratio']:.1%}"
    )
    print(f"Evidence       : {TARGET_SOURCE}")
    print()
    print(f"Pack written   : {output_dir}")


if __name__ == "__main__":
    main()
