# SRH contribution and product scope

## Commercial description

> SRH turns application requirements and measured AI behavior into a documented
> deployment decision: which tested configuration meets the requirements, why,
> and what to investigate when none does.

The value is a repeatable assessment linked to customer requirements, rather than
a generic token-per-second demonstration. The product is intended for multiple
model families; the first validated integration is Docker/vLLM.

## Technical contribution

`Workload Contract → experiments → observed evidence → quality/SLO gates → decision`

SRH implements the contract/schema, planner, fingerprint/evidence format, sample
validation, structured quality replay, selection policy, diagnostic next action,
tests and documentation. Ranking is currently deterministic, not learned.

The current comparison is within the same model/snapshot, image and hardware.
Cross-model comparison requires distinct comparability rules, including handling
of different tokenizers and equivalent task content. General adapter interfaces
and evaluators for other tasks remain future work.

The initial result is deliberately reported as `NO_PROFILE_MEETS_SLO`: all three
profiles were correct on the structured scenario but failed the TTFA requirement.
That is useful decision evidence, not a claim of runtime or model superiority.

## IP and open source

SRH's contribution is the concrete implementation and integration, documentation,
policies and experimental know-how. It is not ownership of the general concept
of benchmarking or configuration selection. No patentability assessment has been
performed; ownership and protectability depend on the actual contributions and
applicable agreements, including for AI-assisted development.

Publishing under Apache-2.0 allows third-party commercial reuse. Commercial value
can grow through maintained integrations, curated scenarios, evidence, delivery
expertise, support and additional services. Upstream model weights, vLLM, blazux
runtime optimizations and NVIDIA components remain their authors' contributions.
