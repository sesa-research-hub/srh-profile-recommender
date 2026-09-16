# Capacity estimator methodology

The capacity planner is a screening instrument. It separates calculated capacity,
performance projections and measured evidence so that a planning estimate cannot be
presented as an observed deployment result.

## Memory capacity

For each hardware, model archetype and weight precision, the planner calculates:

1. weight memory: `total parameters × bits / 8 × 1.08`;
2. KV cache: `2 × layers × KV heads × head dimension × 2 bytes × p95 context × concurrent users`;
3. runtime reserve: the larger of 4 GiB and 12% of weight memory;
4. usable device memory: catalog memory multiplied by a conservative usable fraction.

These values screen impossible combinations. Exact checkpoints can differ because
embedding tables, quantization scales, mixed-precision layers and runtime workspaces
are model-specific. The observed runtime profile remains authoritative.

## Decode rate

Two projection methods are permitted.

### Vendor interactive anchor

When the catalog contains a comparable per-request benchmark, the planner scales it by
active model size and precision. The current H100 dense anchor is NVIDIA's published
Llama 3 8B TensorRT-LLM benchmark at batch size 1, input length 2,048 and output length
512:

- INT4 AWQ: 211.50 output tokens/s;
- FP8: 170.75 output tokens/s;
- FP16: 135.79 output tokens/s.

The same table reports 47.13 output tokens/s for Llama 3 70B INT4 at batch size 1.
The catalog's model-size exponent of 0.7 is the rounded power-law slope between the
published 8B and 70B INT4 points; it is not a fitted value chosen to favor H100.

Source: [NVIDIA TensorRT Model Optimizer Llama 3 PTQ benchmark](https://developer.nvidia.com/blog/post-training-quantization-of-llms-with-nvidia-nemo-and-nvidia-tensorrt-model-optimizer/).

Model-size scaling remains an interpolation rather than an exact checkpoint benchmark
and therefore carries a ±30% planning interval.

### Memory roofline

Without a comparable anchor, single-request decode is estimated as:

`memory bandwidth / effective active weight bytes × explicit efficiency`.

Sparse models add visible routing and resident-expert traffic allowances and carry a
wider ±60% interval. Unanchored dense projections carry ±45%. These coefficients are
versioned SRH assumptions and remain low-confidence until measured on the exact stack.

## Concurrent users

Continuous batching does not divide the single-request token rate by the number of
users. Requests advance together in a batch until compute, memory bandwidth or the
scheduler saturates. The planner therefore calculates:

`per-request rate = single-request rate × retention(concurrency)`

`aggregate rate = per-request rate × concurrent users`.

For H100, the retention value is tied to NVIDIA NIM measurements for Llama 3.1 8B FP8:
inter-token latency changes from 4.77 ms at concurrency 1 to 5.45 ms at concurrency 5.
Source: [NVIDIA NIM LLM benchmarking](https://docs.nvidia.com/nim/benchmarking/llm/latest/performance-tables/llm-1_3-llama3_1-8b.html).

Other hardware retention values remain conservative SRH assumptions and are displayed
as roofline projections in the interface.

## TTFA and completion time

The projected time to first answer uses p95 input tokens, a per-request prefill rate
and a fixed runtime allowance. This remains an SRH planning heuristic even when decode
throughput is anchored to a vendor benchmark; the UI labels the two bases separately.
Completion time is:

`TTFA + p95 output tokens / per-request decode rate`.

The UI always shows p95 input tokens, p95 output tokens and concurrent users beside
the result. A long drafting estimate must not be described as the latency of a short
chat response.

## Shortlist rule

Hard memory, context, precision and power constraints run first. Each selected model
class keeps its best screening tier. Hardware diversity is used only when another
device remains in the same tier and within 15 score points; diversity can no longer
promote a weaker tier into the shortlist.

## Decision boundary

Performance projection decides what to benchmark. It does not certify model quality,
license suitability or production readiness. The final Deployment Recommender accepts
only comparable observed evidence from an exact model, runtime profile, scenario,
evaluator and protocol.
