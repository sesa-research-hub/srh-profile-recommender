# SRH workload-to-deployment architecture

SRH Profile Recommender is evolving from a measured-profile decision engine into a
complete private AI assessment flow.

```text
Client Discovery
      ↓
Client Intake → Workload Translator → Workload Contract
                                      ↓
Hardware Catalog + Model Archetypes + Evidence Catalog
                                      ↓
                              Capacity Planner
                                      ↓
                         Preliminary Candidate Shortlist
                                      ↓
Scenario Builder → Application Adapter → Benchmark Harness
                                      ↓
                              Observed Evidence
                                      ↓
                     Profile + Deployment Recommenders
                                      ↓
                      Verified Deployment Recommendation
```

## Implemented in the capacity-planner alpha

- local client-meeting interface;
- versioned client intake and JSON schema;
- pages/words-to-token translation with an audit trace;
- versioned hardware, model-archetype and evidence catalogs;
- deterministic memory/context/power feasibility;
- low-confidence performance ranges with explicit assumptions;
- hardware-diverse shortlist and benchmark handoff;
- CLI JSON/Markdown dossier generation;
- retained boundary between estimates and observed evidence;
- measured cross-hardware/model deployment comparison with license and cost gates.
- local OpenAI-compatible model discovery and observed readiness tests;
- sourced named-model reference simulations with a strict non-measured label;
- uncertainty-band benchmark priorities with explicit capacity and SLO gates;
- separate sparse-model routing allowance and wider MoE uncertainty, both marked
  as uncalibrated SRH heuristics.
- vendor-interactive performance anchors where comparable evidence exists, with
  continuous-batching retention kept separate from aggregate throughput.

## Existing measured layer

- Workload Contract and Experiment Planner;
- paired probe/assistant execution;
- observed runtime fingerprint;
- raw evidence validation and quality replay;
- quality/SLO gates and deterministic profile selection;
- decision JSON and Markdown.

## Next integration layers

1. Scenario Builder for client documents and expert-validated ground truth.
2. End-to-end RAG adapter with OCR/retrieval/reranking timing and quality.
3. Exact installable checkpoint recipes and automated license-artifact verification.
4. Remote execution adapters for SRH lab, customer site and rented hardware.
5. Calibrated estimator trained only on comparable observed evidence.
6. Commercial cost catalog with dated quotations and lifecycle assumptions.

The staged design lets SRH use preliminary estimates in a meeting without presenting
them as benchmark results. A final decision always remains tied to a scenario,
protocol, evaluator and observed runtime identity.
