# SRH Capacity Planner and measured deployment recommendation

Private AI assessment previously started only after runtime profiles and benchmark
evidence already existed. This change adds the missing path from a client discussion
to a controlled measurement campaign and a final evidence-based decision.

## Result

- captures business-readable demand, document, response, quality and deployment inputs;
- translates them into a versioned SRH Workload Contract;
- screens generic model sizes against memory, context and power constraints;
- presents low-confidence planning ranges separately from measured evidence;
- produces a hardware-diverse shortlist, KPI plan and benchmark handoff;
- compares observed deployments across exact models and hardware with mandatory
  quality, SLO and license gates;
- provides a local meeting interface, JSON/Markdown dossiers and CLI workflows.
- includes the previous measured GB10 campaign as an explicitly scoped UI demo and
  keeps its original workload separate from the current client assessment.
- detects locally served LLMs, executes a controlled readiness benchmark and compares
  only runs with the same workload profile, concurrency and repetition count;
- classifies planning candidates through explainable hard gates and uncertainty bands,
  while keeping vendor-sourced DeepSeek/Mistral simulations visibly non-measured.

## Decision boundary

Capacity projections never become benchmark evidence. A deployment recommendation
requires at least two controlled observed candidates with the same workload,
scenario, evaluator, experiment and protocol. The license decision and commercial
cost provenance remain explicit human-supplied inputs.

## Validation

The complete offline test suite covers translation, feasibility, provenance,
shortlisting, HTTP APIs, measured cross-deployment comparability, license gates and
release safety.
