# SRH Private AI — Profile Recommender

**Sesa Research Hub · first alpha · Apache-2.0 source code**

SRH Profile Recommender turns application requirements and measured evidence into
an explainable decision about an AI runtime configuration. It checks quality and
service objectives before ranking eligible alternatives, and explicitly reports
when no tested configuration is suitable or evidence is insufficient.

The product is intended for multiple model families. The initial integration and
validation use Docker/vLLM; Qwen is the first test backend. Model weights and
inference implementations remain external to the SRH decision engine.

## What is implemented

- Workload contracts describing request sizes, concurrency, quality and latency objectives.
- Experiment planning, including a shared-prefix control at nominal input size/concurrency.
- Observed runtime fingerprints, with comparison before and after measurement.
- Validation of raw samples and replay of the structured enterprise-orders evaluator.
- Quality/SLO gates followed by deterministic ranking: TTFA p95, E2E p95, then minimum answer throughput.
- JSON and Markdown decision evidence with exclusions, scope and next action.
- A Linux/Docker campaign runner that measures three prefill budgets and restores the original container.

The current comparison deliberately requires the **same model/snapshot, image and
hardware**. Cross-model ranking, general runtime plugins and task-specific evaluator
plugins are future work. The alpha is not a trained recommender, a universal model
benchmark or a production certification.

## Quick start: offline validation

Python 3.12 was tested. The core uses only Python's standard library and SRH modules.
No GPU, Docker or model download is required to run the offline tests.

```bash
python3 -m unittest discover -s tests -v
python3 -m srh.recommendation.engine --help
```

Compare three previously collected evidence files:

```bash
python3 -m srh.recommendation.engine \
  srh/workloads/examples/document-rag-sme.json \
  /path/to/baseline.json /path/to/profile-b.json /path/to/profile-c.json \
  --output decision.json --markdown decision.md
```

Live measurements require a configured runtime and a generated scenario pack.
The campaign temporarily stops the source model service. Read the
[operating guide](srh/recommendation/README.md) before running it.

## Initial measured result

Synthetic enterprise-orders RAG, nominal 8k input, concurrency 1, reasoning disabled:

| Prefill token budget | Assistant TTFA p95 | Assistant E2E p95 | Minimum answer tok/s | Structured quality |
|---:|---:|---:|---:|---:|
| 8192 | 5.55 s | 21.66 s | 28.44 | 100% |
| 4096 | 5.95 s | 20.86 s | 26.69 | 100% |
| 16384 | 6.01 s | 23.26 s | 26.52 | 100% |

All three failed the 2-second TTFA p95 objective; other declared gates passed.
A later, separate shared-prefix control on the original profile measured a
1.43-second median and 2.62-second p95 TTFA, still above the p95 target.

There were five repetitions per response mode/profile: 30 measured requests in
the main comparison and 10 in the control, excluding warmups. With five samples,
p95 is the maximum observed value. These observations do not establish statistical
confidence, causal isolation of cache/thermal effects or quality beyond this task.

## Repository boundaries and attribution

| Location | Responsibility |
|---|---|
| `srh/`, `tests/` | SRH workload, evidence and recommendation implementation |
| `scripts/publish_srh_release.py` | SRH release publishing helper |
| `flash`, `Dockerfile*`, `profiles/`, `src/`, other runtime scripts/tools | Retained blazux runtime integration and upstream contributions |
| Model weights, runtime images, CUDA/driver installation | External systems, not relicensed by the SRH source license |

The original runtime documentation is preserved in
[README.upstream.md](README.upstream.md). SRH does not claim authorship of vLLM,
blazux optimizations, model weights, quantization or NVIDIA kernels.

## Documentation and release

- [Architecture, scope and SRH contribution](docs/SRH-POSITIONING.md)
- [License and distribution boundaries](THIRD_PARTY_NOTICES.md)
- [Alpha release notes](docs/releases/0.1.0-alpha.1.md)
- [Publishing procedure](docs/RELEASING.md)
- [Apache-2.0](LICENSE) and [attribution notices](NOTICE)

The Apache license covers applicable source code, not a blanket clearance of all
external models or container dependencies. Model and runtime conditions must be
checked for the particular deployment. Source archives automatically generated
by GitHub contain this whole repository, including retained upstream sources.
