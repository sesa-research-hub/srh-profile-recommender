# Licensing and distribution boundaries

The applicable repository source is distributed under [Apache-2.0](LICENSE),
subject to retained file-level notices and the [NOTICE](NOTICE). The complete
license text replaces the earlier short Apache notice; the original blazux
copyright has been preserved in NOTICE.

## SRH core

The SRH Python core uses standard-library modules and local SRH modules; it does
not import model implementations, vLLM, PyTorch or Transformers. Python remains
subject to its [PSF license and bundled notices](https://docs.python.org/3/license.html).
No Python interpreter is redistributed as an SRH source artifact.

## Retained upstream integration

- [blazux/qwen3.8-Flash-DGX](https://github.com/blazux/qwen3.8-Flash-DGX/blob/main/LICENSE): Apache-2.0 declaration; retained runtime files and documentation are upstream work.
- [vLLM](https://github.com/vllm-project/vllm/blob/main/LICENSE): Apache-2.0; the copied `src/mamba_utils_guarded.py` retains its contributor copyright header.
- Dockerfile downloads from [jschmied/qwen38-flash-next-gb10](https://github.com/jschmied/qwen38-flash-next-gb10): Apache-2.0 LICENSE checked at commits `e0ef69d4f5575dad00d34e05479eaf4c6547bace` and `d9705bde5a5b294478a5baf82b888a64000a16ef`. These fetched build inputs are not part of the independent SRH source core.

## External runtime and model dependencies

[Moby](https://github.com/moby/moby/blob/master/LICENSE),
[vLLM](https://github.com/vllm-project/vllm/blob/main/LICENSE),
[Transformers](https://github.com/huggingface/transformers/blob/main/LICENSE) and
[FlashInfer](https://github.com/flashinfer-ai/flashinfer/blob/main/LICENSE) have
Apache-2.0 licensing at the referenced project level. Runtime distributions may
include components under additional terms. [PyTorch](https://github.com/pytorch/pytorch/blob/main/LICENSE)
has multiple applicable licenses and notices; its complete distribution must be
reviewed rather than assigning a single license to every bundled component.

[NVIDIA CUDA/driver software](https://docs.nvidia.com/cuda/eula/index.html) has its
own terms, including conditions on redistribution. The SRH source license does
not relicense NVIDIA software or external container images. This document is not
a complete container SBOM or clearance of every transitive dependency.

Model licenses are independent of the Recommender. Qwen was the first tested
backend, not a mandatory identity of the product. The measured
[RadixArk revision](https://huggingface.co/RadixArk/Qwen3.8-Flash-Next-NVFP4/blob/7b719225242aacd3dbd3f9407468c2ee9a9d2594/README.md)
refers to the [Qwen model license](https://huggingface.co/Qwen/Qwen3.8-Flash-Next/blob/main/LICENSE).
That model has separate commercial conditions; future models require their own
assessment. This does not change the license of the independent SRH core.

## Release scope

No model weights, Docker images or private machine-specific evidence are added as
release assets. GitHub's automatic source archives contain the full tagged Git
repository, including existing runtime sources and data such as the upstream draft
vocabulary file. They must not be described as exclusively SRH-authored code or as
a fully audited binary inference distribution.

Apache-2.0 permits commercial reuse by others. It preserves attribution and contains
a scoped patent grant; it does not establish exclusivity, patentability or ownership
of third-party work. SRH attribution follows the existing contribution headers.
