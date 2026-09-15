#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
#
# Copyright 2026 Sesa Research Hub
#
# SRH Private AI - Runtime Profile Fingerprinter

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


VERSION = "0.2.0"
SCHEMA = "srh.runtime-profile.v1"


def run_text(args: list[str]) -> str:
    return subprocess.check_output(
        args,
        text=True,
    ).strip()


def docker_inspect(container: str) -> dict[str, Any]:
    raw = run_text([
        "docker",
        "inspect",
        container,
    ])

    data = json.loads(raw)

    if not data:
        raise RuntimeError(
            f"container not found: {container}"
        )

    return data[0]


def process_argv(container: str) -> list[str]:
    raw = subprocess.check_output([
        "docker",
        "exec",
        container,
        "cat",
        "/proc/1/cmdline",
    ])

    return [
        item.decode("utf-8")
        for item in raw.split(b"\0")
        if item
    ]


# Allowlist avoids collecting credentials or unrelated process environment.
RUNTIME_ENV_KEYS = {
    "VLLM_ALLOW_LONG_MAX_MODEL_LEN", "VLLM_FP8_HYBRID", "VLLM_PLE_MMAP",
    "VLLM_PLE_MMAP_PREWARM", "VLLM_PLE_MMAP_WORKERS", "VLLM_QSA_EXACT_TOPK",
    "VLLM_QSA_DETERMINISTIC_TOPK", "VLLM_USE_DEEP_GEMM",
    "VLLM_USE_FLASHINFER_SAMPLER", "VLLM_FP8_PAD_M4", "VLLM_MTP_DRAFT_VOCAB",
    "VLLM_PLE_MMAP_MADVISE", "CUDA_VISIBLE_DEVICES",
}


def process_environment(container: str) -> dict[str, str]:
    raw = subprocess.check_output([
        "docker", "exec", container, "cat", "/proc/1/environ",
    ])
    pairs = [item.decode("utf-8").split("=", 1) for item in raw.split(b"\0") if b"=" in item]
    return {key: value for key, value in pairs if key in RUNTIME_ENV_KEYS}


def get_value(
    argv: list[str],
    flag: str,
) -> str | None:
    try:
        index = argv.index(flag)
    except ValueError:
        return None

    if index + 1 >= len(argv):
        return None

    value = argv[index + 1]

    if value.startswith("-"):
        return None

    return value


def get_multi(
    argv: list[str],
    flag: str,
) -> list[str]:
    try:
        index = argv.index(flag) + 1
    except ValueError:
        return []

    values = []

    while index < len(argv):
        value = argv[index]

        if value.startswith("-"):
            break

        values.append(value)
        index += 1

    return values


def get_inline(
    argv: list[str],
    prefix: str,
) -> str | None:
    for arg in argv:
        if arg.startswith(prefix):
            return arg[len(prefix):]

    return None


def has_flag(
    argv: list[str],
    flag: str,
) -> bool:
    return flag in argv


def find_model_path(
    argv: list[str],
) -> str | None:
    try:
        serve_index = argv.index("serve")
    except ValueError:
        return None

    if serve_index + 1 >= len(argv):
        return None

    return argv[serve_index + 1]


def parse_snapshot(
    model_path: str | None,
) -> dict[str, Any]:
    if not model_path:
        return {}

    result: dict[str, Any] = {
        "path": model_path,
    }

    marker = "/snapshots/"

    if marker in model_path:
        prefix, snapshot = model_path.split(
            marker,
            1,
        )

        result["snapshot"] = snapshot

        repo_component = prefix.rsplit("/", 1)[-1]

        if repo_component.startswith("models--"):
            repo = repo_component[len("models--"):].replace(
                "--",
                "/",
            )
            result["repository"] = repo

    return result


def model_api_info(
    base_url: str,
    served_names: list[str],
) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(
            f"{base_url.rstrip('/')}/models",
            timeout=15,
        ) as response:
            data = json.loads(response.read())

        models = data.get("data", [])

        matched = [
            model
            for model in models
            if model.get("id") in served_names
        ]

        return {
            "matched": matched,
            "all_model_ids": [
                model.get("id")
                for model in models
            ],
        }

    except Exception as exc:
        return {
            "error": str(exc),
        }


def gpu_info() -> dict[str, Any]:
    try:
        output = run_text([
            "nvidia-smi",
            "--query-gpu=name,driver_version",
            "--format=csv,noheader",
        ])

        first = output.splitlines()[0]
        name, driver = [
            part.strip()
            for part in first.split(",", 1)
        ]

        return {
            "name": name,
            "driver": driver,
        }

    except Exception as exc:
        return {
            "error": str(exc),
        }


def parse_speculative(
    argv: list[str],
) -> Any:
    raw = get_value(
        argv,
        "--speculative-config",
    )

    if raw is None:
        return None

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def canonical_sha256(
    value: dict[str, Any],
) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")

    return hashlib.sha256(encoded).hexdigest()


def capture_profile(
    container: str,
    base_url: str,
) -> dict[str, Any]:
    """
    Capture the effective live runtime profile.

    The profile hash contains only execution identity:
    hardware, immutable container image and observed process/runtime
    configuration. Timestamps and container names are excluded.
    """

    inspect = docker_inspect(container)
    argv = process_argv(container)

    served_names = get_multi(
        argv,
        "--served-model-name",
    )

    model_path = find_model_path(argv)
    snapshot = parse_snapshot(model_path)
    speculative = parse_speculative(argv)

    effective = {
        "engine": "vllm",
        "argv": argv,
        "process_environment": process_environment(container),

        "model": {
            **snapshot,
            "served_names": served_names,
            "load_format": get_value(
                argv,
                "--load-format",
            ),
        },

        "scheduler": {
            "max_model_len": get_value(
                argv,
                "--max-model-len",
            ),
            "max_num_seqs": get_value(
                argv,
                "--max-num-seqs",
            ),
            "max_num_batched_tokens": get_value(
                argv,
                "--max-num-batched-tokens",
            ),
            "chunked_prefill": has_flag(
                argv,
                "--enable-chunked-prefill",
            ),
        },

        "memory": {
            "gpu_memory_utilization": get_value(
                argv,
                "--gpu-memory-utilization",
            ),
            "kv_cache_dtype": get_value(
                argv,
                "--kv-cache-dtype",
            ),
        },

        "cache": {
            "prefix_caching": has_flag(
                argv,
                "--enable-prefix-caching",
            ),
        },

        "execution": {
            "cudagraph_mode": get_inline(
                argv,
                "-cc.cudagraph_mode=",
            ),
            "splitting_ops": get_inline(
                argv,
                "-cc.splitting_ops=",
            ),
            "flashinfer_autotune_disabled": has_flag(
                argv,
                "--no-enable-flashinfer-autotune",
            ),
            "speculative_config": speculative,
        },

        "capabilities": {
            "auto_tool_choice": has_flag(
                argv,
                "--enable-auto-tool-choice",
            ),
            "tool_call_parser": get_value(
                argv,
                "--tool-call-parser",
            ),
            "reasoning_parser": get_value(
                argv,
                "--reasoning-parser",
            ),
        },
    }

    execution_identity = {
        "hardware": {
            "architecture": platform.machine(),
            "gpu": gpu_info(),
        },

        "container_image": {
            "image_name": (
                inspect.get("Config", {})
                .get("Image")
            ),
            "image_id": inspect.get("Image"),
        },

        "effective_runtime": effective,
    }

    profile_hash = canonical_sha256(
        execution_identity
    )

    profile_id = (
        "srh-"
        + profile_hash[:12]
    )

    return {
        "schema": SCHEMA,
        "capture_version": VERSION,
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),

        "profile_id": profile_id,
        "profile_sha256": profile_hash,

        "container": {
            "name": container,
            "created": inspect.get("Created"),
            "restart_count": inspect.get(
                "RestartCount"
            ),
        },

        "execution_identity": execution_identity,

        "observed_api": model_api_info(
            base_url,
            served_names,
        ),

        "provenance": {
            "source": "live-runtime",
            "effective_process_observed": True,
            "declared_environment_used": False,
        },
    }


def write_profile(
    report: dict[str, Any],
) -> Path:
    output_dir = (
        Path(__file__).resolve().parent
        / "captured"
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    output = (
        output_dir
        / f"{report['profile_id']}.json"
    )

    output.write_text(
        json.dumps(
            report,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    return output


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Capture and fingerprint an effective SRH runtime profile"
        )
    )

    parser.add_argument(
        "container",
        help="Running Docker container name or ID",
    )

    parser.add_argument(
        "--base-url",
        default="http://localhost:18300/v1",
    )

    args = parser.parse_args()

    report = capture_profile(
        args.container,
        args.base_url,
    )

    output = write_profile(report)

    identity = report["execution_identity"]
    effective = identity["effective_runtime"]
    model = effective["model"]
    speculative = effective["execution"]["speculative_config"]

    print("SRH RUNTIME PROFILE")
    print("=" * 72)
    print(f"Capture     : {VERSION}")
    print(f"Profile ID  : {report['profile_id']}")
    print(f"SHA256      : {report['profile_sha256']}")
    print()
    print(
        "Image       : "
        f"{identity['container_image']['image_name']}"
    )
    print(
        "Image ID    : "
        f"{identity['container_image']['image_id']}"
    )
    print(
        "GPU         : "
        f"{identity['hardware']['gpu'].get('name')}"
    )
    print(
        "Driver      : "
        f"{identity['hardware']['gpu'].get('driver')}"
    )
    print(
        "Snapshot    : "
        f"{model.get('snapshot')}"
    )
    print(
        "Context     : "
        f"{effective['scheduler']['max_model_len']}"
    )
    print(
        "Sequences   : "
        f"{effective['scheduler']['max_num_seqs']}"
    )
    print(
        "GPU memory  : "
        f"{effective['memory']['gpu_memory_utilization']}"
    )
    print(
        "Prefix cache: "
        f"{effective['cache']['prefix_caching']}"
    )
    print(
        "Chunked PF  : "
        f"{effective['scheduler']['chunked_prefill']}"
    )
    print(
        "MTP         : "
        f"{speculative.get('num_speculative_tokens') if isinstance(speculative, dict) else None}"
    )
    print()
    print(f"Manifest    : {output}")


if __name__ == "__main__":
    main()
