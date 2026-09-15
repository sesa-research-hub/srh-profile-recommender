## Problem and resulting behavior

Deployment choices need to be evaluated against an application's quality and
service objectives, with evidence tied to the runtime that was actually measured.
This change adds the SRH workload-to-decision flow: contracts, experiment planning,
paired measurements, runtime fingerprints, raw sample validation and explicit
quality/SLO gates before ranking eligible configurations.

A correct response alone does not imply deployment suitability. The initial
three-profile comparison reports `NO_PROFILE_MEETS_SLO` because all configurations
miss the 2-second TTFA p95 objective despite 100% structured scenario correctness.

## Release preparation

- Make the root README describe SRH and retain upstream instructions separately.
- Include the full Apache-2.0 text and preserve blazux/vLLM attributions.
- Document model/runtime boundaries and the alpha's same-model scope.
- Add an authenticated publishing helper with exact-commit validation and safe retries.

## Validation

Offline functional and publishing-helper tests pass. Real measurements cover three
prefill configurations on GB10 (30 measured requests) and a later shared-prefix
control (10 measured requests). The original container was restored with the same
fingerprint. Results are exploratory, scoped to one structured task and one model
family; cross-model ranking and general evaluator plugins remain future work.
