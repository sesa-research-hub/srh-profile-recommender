#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
#
# Copyright 2026 Sesa Research Hub
#
# SRH Private AI - Hardware Characterization
#
# Collects a reproducible hardware/runtime fingerprint of the host
# without modifying the system.

from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path


SRH_COMPONENT = "SRH Hardware Characterization"
SRH_VERSION = "0.1.0"


def run(cmd: list[str]) -> str | None:
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
            check=False,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def bytes_to_gib(value: int) -> float:
    return round(value / (1024 ** 3), 2)


def system_memory() -> dict:
    total_kb = None
    available_kb = None

    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    total_kb = int(line.split()[1])
                elif line.startswith("MemAvailable:"):
                    available_kb = int(line.split()[1])
    except OSError:
        pass

    return {
        "total_gib": round(total_kb / 1024 / 1024, 2) if total_kb else None,
        "available_gib": round(available_kb / 1024 / 1024, 2)
        if available_kb
        else None,
    }


def gpu_info() -> dict:
    """
    Collect NVIDIA GPU information.

    GB10 uses coherent unified memory, so nvidia-smi may report
    memory.total / memory.free as [N/A]. This is expected and must
    not be interpreted as a GPU detection failure.
    """

    result = run(
        [
            "nvidia-smi",
            "--query-gpu=name,driver_version,memory.total,memory.free",
            "--format=csv,noheader,nounits",
        ]
    )

    if not result:
        return {
            "detected": False,
            "name": None,
            "driver": None,
            "memory_total_mib": None,
            "memory_free_mib": None,
            "memory_reporting": "unavailable",
        }

    first_line = result.splitlines()[0]
    fields = [x.strip() for x in first_line.split(",")]

    def parse_number(value: str):
        value = value.strip()

        if value.upper() in {
            "[N/A]",
            "N/A",
            "NA",
            "NOT SUPPORTED",
            "",
        }:
            return None

        try:
            return float(value)
        except ValueError:
            return None

    name = fields[0] if len(fields) > 0 else None
    driver = fields[1] if len(fields) > 1 else None
    memory_total = parse_number(fields[2]) if len(fields) > 2 else None
    memory_free = parse_number(fields[3]) if len(fields) > 3 else None

    unified_memory = bool(
        name and "GB10" in name.upper()
    )

    return {
        "detected": True,
        "name": name,
        "driver": driver,
        "memory_total_mib": memory_total,
        "memory_free_mib": memory_free,
        "unified_memory": unified_memory,
        "memory_reporting": (
            "system_unified_memory"
            if unified_memory and memory_total is None
            else "nvidia_smi"
        ),
        "raw": first_line,
    }


def cuda_info() -> dict:
    nvcc = run(["nvcc", "--version"])
    nvidia_smi = run(["nvidia-smi"])

    return {
        "nvcc_available": nvcc is not None,
        "nvcc": nvcc,
        "nvidia_smi_available": nvidia_smi is not None,
    }


def docker_info() -> dict:
    """
    Detect Docker and NVIDIA container GPU access.

    Modern Docker installations can expose NVIDIA GPUs through CDI
    even when the legacy 'nvidia' Docker runtime is not registered.
    """

    version = run(["docker", "--version"])
    runtimes = run(["docker", "info", "--format", "{{json .Runtimes}}"])
    default_runtime = run(
        ["docker", "info", "--format", "{{.DefaultRuntime}}"]
    )

    legacy_runtime = bool(
        runtimes and '"nvidia"' in runtimes.lower()
    )

    nvidia_ctk_version = run(["nvidia-ctk", "--version"])
    cdi_output = run(["nvidia-ctk", "cdi", "list"])

    cdi_devices = []

    if cdi_output:
        for line in cdi_output.splitlines():
            line = line.strip()
            if line.startswith("nvidia.com/"):
                cdi_devices.append(line)

    cdi_spec_paths = [
        Path("/var/run/cdi/nvidia.yaml"),
        Path("/etc/cdi/nvidia.yaml"),
    ]

    cdi_spec = next(
        (str(p) for p in cdi_spec_paths if p.exists()),
        None,
    )

    cdi_available = bool(cdi_devices and cdi_spec)

    if cdi_available:
        gpu_container_access = "cdi"
    elif legacy_runtime:
        gpu_container_access = "legacy_nvidia_runtime"
    else:
        gpu_container_access = "not_detected"

    return {
        "available": version is not None,
        "version": version,
        "default_runtime": default_runtime,

        "nvidia": {
            "container_access": gpu_container_access,

            "legacy_runtime": {
                "detected": legacy_runtime,
            },

            "cdi": {
                "available": cdi_available,
                "toolkit_version": nvidia_ctk_version,
                "spec_file": cdi_spec,
                "devices": cdi_devices,
            },
        },
    }


def storage_info(path: Path) -> dict:
    usage = shutil.disk_usage(path)

    mount = run(
        [
            "findmnt",
            "-no",
            "SOURCE,FSTYPE,TARGET",
            "-T",
            str(path),
        ]
    )

    lsblk = run(
        [
            "lsblk",
            "-J",
            "-o",
            "NAME,KNAME,TYPE,SIZE,ROTA,MODEL,TRAN,MOUNTPOINTS",
        ]
    )

    parsed_lsblk = None
    if lsblk:
        try:
            parsed_lsblk = json.loads(lsblk)
        except json.JSONDecodeError:
            parsed_lsblk = lsblk

    return {
        "path": str(path),
        "capacity_gib": bytes_to_gib(usage.total),
        "available_gib": bytes_to_gib(usage.free),
        "mount": mount,
        "block_devices": parsed_lsblk,
    }


def detect_hardware_class(gpu: dict, memory: dict) -> str:
    gpu_name = (gpu.get("name") or "").upper()
    total_ram = memory.get("total_gib") or 0

    if "GB10" in gpu_name and total_ram >= 110:
        return "GB10-128"

    if "GB10" in gpu_name:
        return "GB10"

    if gpu.get("detected"):
        return "NVIDIA-GENERIC"

    return "UNKNOWN"


def baseline_profile(hardware_class: str) -> dict | None:
    """
    This is intentionally NOT an autotuner yet.

    GB10-128 values represent the SRH experimentally validated baseline.
    Later releases will replace static selection with benchmark-driven
    profile optimization.
    """

    if hardware_class == "GB10-128":
        return {
            "profile": "srh-gb10-balanced-baseline",
            "status": "experimentally_validated_baseline",
            "mode": "hybrid",
            "gpu_mem": 0.80,
            "ctx": 262144,
            "seqs": 8,
            "mtp": 2,
            "prefix_cache": True,
            "exact_topk": True,
            "prewarm": True,
        }

    return None


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    report_dir = repo_root / "srh" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)

    memory = system_memory()
    gpu = gpu_info()
    hardware_class = detect_hardware_class(gpu, memory)

    report = {
        "schema": "srh.hardware-characterization.v1",
        "srh": {
            "component": SRH_COMPONENT,
            "version": SRH_VERSION,
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "host": {
            "hostname": socket.gethostname(),
            "architecture": platform.machine(),
            "kernel": platform.release(),
            "os": platform.platform(),
        },
        "memory": memory,
        "gpu": gpu,
        "cuda": cuda_info(),
        "docker": docker_info(),
        "storage": storage_info(repo_root),
        "classification": {
            "hardware_class": hardware_class,
        },
        "recommended_baseline": baseline_profile(hardware_class),
    }

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    hostname = socket.gethostname().replace("/", "_")

    output = report_dir / f"characterization-{hostname}-{timestamp}.json"

    with open(output, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print()
    print("SRH PRIVATE AI")
    print("=" * 60)
    print(f"Component       : {SRH_COMPONENT}")
    print(f"Version         : {SRH_VERSION}")
    print(f"Hostname        : {report['host']['hostname']}")
    print(f"Architecture    : {report['host']['architecture']}")
    print(f"GPU             : {gpu.get('name')}")
    print(f"Driver          : {gpu.get('driver')}")
    print(f"System RAM      : {memory.get('total_gib')} GiB")
    print(f"Available RAM   : {memory.get('available_gib')} GiB")
    print(f"Hardware class  : {hardware_class}")

    baseline = report["recommended_baseline"]

    if baseline:
        print()
        print("SRH validated baseline")
        print("-" * 60)
        print(f"MODE            : {baseline['mode']}")
        print(f"GPU_MEM         : {baseline['gpu_mem']}")
        print(f"CTX             : {baseline['ctx']}")
        print(f"SEQS            : {baseline['seqs']}")
        print(f"MTP             : {baseline['mtp']}")
        print(f"PREFIX_CACHE    : {int(baseline['prefix_cache'])}")
        print(f"EXACT_TOPK      : {int(baseline['exact_topk'])}")
        print(f"PREWARM         : {int(baseline['prewarm'])}")
        print()
        print("NOTE: this is a validated baseline, not yet an autotuned profile.")

    print()
    print(f"Report written  : {output}")
    print()


if __name__ == "__main__":
    main()
