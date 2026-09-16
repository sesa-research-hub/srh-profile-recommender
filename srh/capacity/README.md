# SRH Private AI Capacity Planner

The Capacity Planner turns business-readable discovery inputs into a preliminary,
auditable shortlist of private AI deployment candidates. It is designed for use
during a client meeting and feeds the existing measurement workflow; it does not
turn estimates into deployment evidence.

## Run the meeting interface

From the repository root:

```bash
python3 -m srh.capacity.server --open
```

Open `http://127.0.0.1:8765` if the browser does not open automatically. The server
binds to localhost by default, uses Python's standard library and does not persist
client inputs. The browser can download the capacity dossier as JSON or print the
results to PDF. No client documents are uploaded by this interface. After laboratory
tests, the same page can import a candidate manifest and observed evidence JSON files
to run the measured Deployment Recommender locally.

The measured section provides separate selectors for the first two evidence files,
plus an optional multi-file selector. **Load measured Qwen demo** loads the three
observed GB10 profiles from the first SRH campaign together with their original
Workload Contract and the bundled test manifest. This demonstrates the complete UI
flow without presenting those measurements as evidence for the current client.

## Test a model already running on the machine

After calculating the client shortlist, open **Test real models and build the
recommendation**. The interface discovers OpenAI-compatible endpoints bound to
localhost, currently including the vLLM endpoint on port `18300`. For each selected
model choose:

- quick, representative or stress input profile;
- one, three or five repetitions;
- one to eight concurrent users.

The readiness test sends a synthetic Italian construction-tender scenario sized from
the current Workload Contract. It observes first-answer latency, complete latency,
answer throughput, request errors and deterministic extraction/citation quality. Runs
with the same profile, concurrency and repetition count form one comparable session;
quality and every SLO gate are applied before latency ranking.

This is real local inference evidence for the synthetic readiness scenario. It is not
client acceptance evidence: the final campaign still requires client documents,
expert-validated ground truth, OCR/retrieval components and the exact application.
Only loopback HTTP endpoints are accepted by the local runner.

## Simulate a named model not installed

The same selector includes DeepSeek V4 Flash/Pro and Mistral Small 4/Large 3 from a
versioned reference catalog. Their parameter counts, context, license labels and
published benchmark notes link to vendor sources. Selecting one runs the SRH capacity
projection against the chosen hardware. The UI labels this output as a reference
simulation: vendor benchmarks on unrelated tasks never become SRH measurements or a
prediction of client quality.

Bundled files:

- `examples/qwen-measured-demo-manifest.json`;
- `examples/qwen-measured-demo/baseline-8192.json`;
- `examples/qwen-measured-demo/prefill-4096.json`;
- `examples/qwen-measured-demo/prefill-16384.json`.

The bundled construction-tender example can also be processed from the CLI:

```bash
python3 -m srh.capacity.planner \
  srh/capacity/examples/construction-tenders-intake.json \
  --output results/construction-tenders.capacity-plan.json \
  --markdown results/construction-tenders.capacity-plan.md
```

## Pipeline and decision boundary

1. **Client intake** records people, demand, document sizes, response length,
   experience targets, data location and the alternatives to explore.
2. **Workload translation** converts pages and words to explicit token assumptions
   and produces a validated `srh.workload-contract.v1`.
3. **Capacity screening** calculates weight, KV-cache and runtime-reserve memory.
   Hard memory, context and power blockers eliminate impossible combinations.
4. **Performance projection** produces a broad planning range using hardware
   bandwidth, active model parameters, concurrency and explicit SRH heuristic
   coefficients. Its confidence is `LOW` until comparable measurements calibrate it.
   Candidates are classified as strong, conditional, borderline, unlikely or
   infeasible from hard gates plus conservative, point and optimistic uncertainty
   bands.
5. **Candidate generation** selects up to eight feasible candidates, preferring
   every selected model archetype first and hardware diversity where possible. The
   meeting UI also exposes the full candidate matrix and the reason for exclusions.
6. **Benchmark handoff** generates the existing representative experiment plan.
   Exact model snapshot, container, runtime and scenario pack must be resolved before
   execution.
7. **Profile Recommender** tunes multiple runtime profiles within one fixed deployment.
8. **Deployment Recommender** compares observed candidates across hardware and exact
   models, holding scenario, evaluator, experiment and protocol constant. Quality,
   all SLOs and an approved license review remain mandatory gates.

Capacity output therefore carries:

```text
PRELIMINARY_CAPACITY_SCREENING → MEASUREMENT_REQUIRED
```

It is deliberately incompatible with measured Recommender evidence. After execution,
the resulting observations can be evaluated by the Deployment Recommender. This
prevents an estimate from being relabelled as a benchmark.

## Compare measured deployments

The measured deployment layer accepts a Workload Contract, at least two observed
evidence files and a `srh.deployment-candidates.v1` manifest. The manifest supplies
human-reviewed labels, license status, dated cost provenance and device power.

```bash
python3 -m srh.recommendation.deployment \
  workload.json deployment-candidates.json candidate-a.evidence.json candidate-b.evidence.json \
  --output deployment-decision.json --markdown deployment-decision.md
```

Two deterministic policies are available in the manifest:

- `performance_first`: quality/SLO/license gates, then TTFA, E2E, throughput and cost;
- `cost_first`: quality/SLO/license gates, then three-year cost, TTFA and E2E.

Hardware, exact model, engine and image may differ. Experiment, scenario, embedded
ground truth, evaluator, response policy and measurement protocol must be identical.

## Inputs a client can answer

- total and simultaneous users;
- requests per day and peak requests per minute;
- typical, demanding and maximum document pages;
- pages likely to be retrieved for each answer;
- typical, demanding and maximum response length in words;
- acceptable first-useful and complete response times;
- citation and grounding requirements;
- data residency and maximum device power;
- model families the client or SRH wants to evaluate.

The planner explores generic model **archetypes** for sizing: dense 3B, 8B, 14B,
32B and 70B, plus sparse MoE classes from 30B/3B active through 235B/22B active.
Names such as Qwen,
DeepSeek or Llama are carried into the benchmark handoff, but the planner makes no
quality or licensing claim about them. They must be resolved to exact checkpoints
and evaluated on the client scenario.

## KPI model

Calculated capacity:

- usable accelerator/system memory;
- model weight memory including a planning overhead;
- KV-cache memory at p95 context and declared concurrency;
- runtime reserve and remaining headroom;
- model context and device power feasibility.

Estimated performance:

- first useful response p95;
- complete response p95;
- answer tokens per second per active user;
- aggregate answer throughput.

Not simulated and required from the benchmark:

- domain quality, omissions, hallucinations and citation correctness;
- OCR, retrieval and reranking quality;
- observed latency, errors, resource use and sustained stability;
- exact model/runtime compatibility and licensing suitability.

## Provenance

The product keeps evidence types separate:

| Type | Meaning |
|---|---|
| `CALCULATED` | Deterministic calculation from declared inputs and catalog fields |
| `ESTIMATED` | Heuristic planning range; never final evidence |
| `VENDOR_REPORTED` | Hardware specification from its vendor |
| `PUBLIC_BENCHMARK` | Externally published result, when added with source metadata |
| `SRH_MEASURED` | Measurement executed by SRH |
| `CUSTOMER_SITE_MEASURED` | Measurement executed in the target deployment |

The starter hardware catalog records NVIDIA's published specifications for the
GB10 128 GB class, RTX PRO 6000 Blackwell 96 GB and H100 SXM 80 GB. Planning
efficiency coefficients are separately identified as SRH assumptions. Sources:

- <https://docs.nvidia.com/dgx/dgx-spark/>
- <https://www.nvidia.com/products/workstations/professional-desktop-gpus/rtx-pro-6000/>
- <https://www.nvidia.com/data-center/h100/>

Hardware prices are intentionally absent: quotes, system configuration, support,
taxes and availability change over time. Cost modeling should use a dated commercial
catalog rather than an embedded constant.

## Current limitations

- named reference entries are sourced planning inputs, not installed checkpoints or
  measured client-quality profiles;
- performance is a low-confidence range and is not yet statistically calibrated;
- the web UI does not ingest documents or build scenario packs;
- OCR, vector retrieval and application latency are outside the current projection;
- the existing live campaign remains Docker/vLLM-specific and automates a narrow
  same-model prefill-budget comparison;
- cross-model quality comparison requires evaluator plugins and observed runs.

These limits are shown in both JSON output and the meeting interface.
