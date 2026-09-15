# SRH Profile Recommender

Compare at least three distinct observed runtime configurations of the same
model snapshot, container image and hardware against a workload contract and
one experiment. SRH provides workload decisions; the existing upstream runtime
continues serving the model. This MVP is an exploratory deployment recommender,
not a model modification or a statistical performance certification.

## Run a campaign

From the repository root, first preview the campaign:

```bash
python3 -m srh.recommendation.campaign \
  srh/workloads/plans/document-rag-sme.plan.json baseline-p50-c1 \
  srh/scenarios/generated/enterprise-orders-rag-v3/8000 \
  --output-dir srh/recommendation/results/campaign-001
```

Add `--execute` to run it. The source must be a running bridge-networked Docker
container, default `qwen38-flash`, publishing container port 8000. The harness:

1. Saves a private container inspection (mode 0600) and a campaign journal.
2. Measures the current runtime with five paired probe/assistant repetitions.
3. Stops and preserves the source container, including its writable layer.
4. Creates two variants from the same image and configuration, changing only
   `--max-num-batched-tokens` to 4096 and 16384. They run sequentially on
   `127.0.0.1:18301`, with automatic restarts disabled.
5. Waits for the model API before each measurement and retains stopped variants.
6. Restarts the source and verifies API readiness, including on ordinary failures
   and SIGTERM/KeyboardInterrupt. SIGKILL or host failure cannot execute cleanup;
   use `campaign.json` to identify and stop an active variant before restarting
   the original container. The journal marks restoration only after readiness.

The original API is unavailable while variants run. Model reloads on this DGX
can take around 8–13 minutes each. Other applications/containers are not stopped.
Avoid unrelated model traffic during measurement. The output directory must be
new; source configuration may contain secrets and must not be published.

## Capture evidence manually

For any separately configured runtime, use the same experiment and scenario pack:

```bash
python3 -m srh.experiments.paired_modes \
  srh/workloads/plans/document-rag-sme.plan.json baseline-p50-c1 \
  srh/scenarios/generated/enterprise-orders-rag-v3/8000 \
  --container qwen38-flash --base-url http://localhost:18300/v1 \
  --repetitions 5 --seed 42
```

Evidence includes pack digest, embedded ground truth, response/quality policies,
evaluator digest, executor version, seed and runtime fingerprints before/after.
Fingerprints include an allowlist of observed process environment settings,
without credentials. A changed fingerprint blocks recommendation. Container/API
routing must be verified by the operator; a matching model name is not attestation.
Do not relabel one measurement as multiple profiles. Cache state is an experiment
condition: changing it requires a separate cohort, not a runtime-profile win.

## Produce a decision

```bash
python3 -m srh.recommendation.engine \
  srh/workloads/examples/document-rag-sme.json \
  srh/recommendation/results/campaign-001/baseline-8192.json \
  srh/recommendation/results/campaign-001/prefill-4096.json \
  srh/recommendation/results/campaign-001/prefill-16384.json \
  --output srh/recommendation/results/decision.json \
  --markdown srh/recommendation/results/decision.md
python3 -m unittest discover -s tests -v
```

The engine checks the workload plan, response/reasoning policy, profile hashes,
scenario and protocol comparability. It re-evaluates raw answers using the
versioned structured enterprise-orders evaluator and recomputes metrics/counts
from raw request batches. Missing samples, nonfinite metrics, changed evaluators
and inconsistent profiles cannot produce a recommendation. Truncated output is
a quality failure even when it contains the correct structured record.

Quality and every SLO must pass in the contract's default response mode. Among
eligible profiles, minimize TTFA p95, then E2E p95, then maximize minimum observed
answer token rate. Exact ties use profile hash for repeatability, not evidence
of superiority. Probe data supports diagnosis, not substitution for assistant
quality. The policy is explicit and latency-first; it is not a learned optimizer.

JSON and Markdown retain candidate checks, blockers, source paths, evidence and
contract hashes, comparison failures and next action. Verdicts:

- `RECOMMENDED`: at least three valid comparable profiles; one or more eligible.
- `NO_PROFILE_MEETS_SLO`: valid comparison, but no profile meets all gates.
- `INSUFFICIENT_EVIDENCE`: invalid/missing evidence, differing cohorts or fewer
  than three distinct profiles. Duplicates block selection; select one campaign
  per profile explicitly. Historical evidence without metadata is not upgraded
  retroactively.

## Limits

The verdict applies only to the tested experiment (e.g. 8k/c1/warm), not all planned
experiments in the workload plan. Five repetitions do not establish tail-latency
confidence or significance. Sequential runs include thermal/filesystem-cache
variation; prompt nonce values vary between campaigns. Measurements themselves
are trusted harness observations. Hashes detect inconsistency, not fabrication;
model weight files and container writable layers are not byte-attested.

Semantic evaluation supports the supplied structured enterprise-orders task,
not general RAG quality or arbitrary tool use. Full workload certification,
randomized cross-day repeats, multiple scenario packs and routing attestation
remain future extensions. Synthetic unit fixtures are never deployment evidence.

When the only blocker is TTFA, the planner now offers `shared-prefix-p50-c1`:
it holds nominal input size and concurrency equal to baseline. Generate a fresh
plan with `python3 -m srh.workloads.planner --help` for the output options.
This application-context control belongs to a separate evidence cohort.
