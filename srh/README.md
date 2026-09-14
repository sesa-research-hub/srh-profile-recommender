# SRH Private AI Components

This directory contains original components developed by
Sesa Research Hub for private and sovereign AI deployments.

## Hardware Characterization

`characterize.py` creates a reproducible hardware and runtime
fingerprint of the system.

The first supported hardware class is NVIDIA GB10 with 128 GB
coherent unified memory, including NVIDIA DGX Spark and compatible
GB10 systems.

The component detects:

- operating system and architecture
- system and unified memory
- NVIDIA GPU and driver
- CUDA environment
- Docker environment
- NVIDIA GPU container access via CDI or legacy runtime
- storage configuration
- SRH hardware classification

For recognized systems it can associate an experimentally validated
SRH baseline profile.

The baseline is not an autotuning result. Subsequent SRH components
will benchmark the machine and derive workload-specific profiles.
