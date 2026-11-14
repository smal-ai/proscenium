# Read-only lower-memory proposal, no allocation or model load

**No safe fit is established with the currently installed choices.** The original
`w07-continuation-b` is already closed and its actual outcome committed as `c72ec701`.
It was not reopened, polled or resubmitted for this analysis. The unrelated SQL Server,
Blender, original Qwen and all independent services remain untouched.

[Measured header inventory and arithmetic](lower-memory-analysis.json) were produced by
`tests/native/providers/m07_memory_inventory.py`. It reads only safetensors headers/stat,
installed loader source hashes, `/proc/meminfo` and original receipt hashes. It does not
import torch, read tensor payloads, load a model, call a provider or modify a workflow.

## Existing files: measured, not inferred from names

| Installed file | File bytes | Tensor payload storage |
| --- | ---: | --- |
| `flux-2-klein-4b.safetensors` | 7751105712 | 7751089152 bytes BF16. |
| `flux-2-klein-9b-kv-fp8.safetensors` | 9818935984 | 8338276352 bytes FP8, 1480609792 bytes BF16, 840 bytes F32. |
| `qwen_3_4b.safetensors` | 8044982048 | 8044936192 bytes BF16. |
| `qwen_3_8b_fp8mixed.safetensors` | 8664848742 | Mixed FP8/BF16/U8/F32; **619866694 bytes larger** than the 4B encoder file. |
| `flux2-vae.safetensors` | 336213556 | 336185484 bytes F32 plus 8 bytes I64. |

The newer host snapshot measured **14091120640 bytes MemAvailable**. That is below the
original unchanged **20427268612-byte** cold-fit floor. Header reads are not runtime peak
measurements, and file size alone cannot certify inference fit.

## Officially supported mechanism worth a future bounded investigation

[Official ComfyUI loader source](https://raw.githubusercontent.com/Comfy-Org/ComfyUI/master/nodes.py)
and the inspected installed `nodes.py:983–1005` both expose:

```json
{"UNETLoader.weight_dtype": "fp8_e4m3fn"}
```

The loader maps that option to `torch.float8_e4m3fn` for the **diffusion model only**.
The `fast` variant additionally enables FP8 optimisations; it does not offer a smaller
one-byte weight format. The plain setting is the narrower future candidate. Support for
the advertised option is not a measured ROCm execution/quality guarantee.

The [official Klein guide](https://docs.comfy.org/tutorials/flux/flux-2-klein) pairs 4B
models with `qwen_3_4b` and 9B models with `qwen_3_8b_fp8mixed`. It publishes pre-quantised
4B FP8 download choices, but the inspected installed 4B file is BF16. No download,
conversion file or replacement is authorised or performed here. Runtime casting the
existing file is not proof of the same loading peak as a pre-quantised file.

### Arithmetic for a new, explicit 4B-FP8 graph

Keeping the current 4B encoder/VAE and assuming ideal one-byte storage for all diffusion
weights:

- Ideal packed diffusion payload: **3875544576 bytes**, half its BF16 payload.
- Unchanged 4B text-encoder payload: **8044936192 bytes**.
- Unchanged VAE payload: **336185492 bytes**.
- Protected host headroom: **4294967296 bytes**.
- Nominal all-resident payload plus headroom: **16551633556 bytes**.
- Shortfall against the new host observation: **2460512916 bytes**.

This estimate excludes casting overlap with the original BF16 state, scales, activations,
allocator effects and caches. It is **not a rigorous lower bound**: normal mmap/offload and
component residency can differ. Nor is it a safe admission floor: conversion/loading peaks
can exceed the nominal value. No such residency/peak was measured in this analysis.

Comfy's official guide quotes about 8.4 GB VRAM for distilled 4B on an RTX 5090. That is
not a host-RAM fit guarantee for this shared-memory AMD workstation with other resident
processes and protected headroom. It cannot replace either the current host guard or an
allocated measurement of a different graph's loading peak.

## Alternatives that do not solve the observed blocker

- The installed 8B FP8-mixed encoder is **larger**, not a smaller substitute for the 4B
  encoder, and the official model pairing is different. Do not mix it into the original
  4B graph under a filename-based memory assumption.
- The installed 9B KV-FP8 file plus its 8B encoder, VAE and four-GiB headroom totals
  **23114965578 file bytes**. It is not the memory-saving candidate. The exact KV variant's
  execution/quality compatibility was not tested.
- `CLIPLoader.device="cpu"` sets both its load/offload devices to CPU in the inspected
  source. It changes placement, not weight precision. On this shared-host-memory system
  it does not establish a total host-RAM saving; runtime CPU dtype/residency may also vary.
- Smaller image dimensions do not remove the large fixed encoder/diffusion weight cost.
  The original job is already 64×64 and cannot be changed in place anyway.
- `fp8_e5m2` has the same nominal one-byte storage, not another factor-of-two reduction.
  Fast FP8 kernel behaviour on this device is unproven here.

## Proposed disposition

Retain the original job unchanged. If the coordinator later authorises a **new native
generation task and graph**, plain diffusion `fp8_e4m3fn` with the existing paired 4B
encoder/VAE is the supported lower-memory candidate to evaluate. Do not claim it fits
now, silently lower the existing threshold, or apply the changed dtype to the original
frozen request. Its future memory admission must account for conversion/loading peak and
the four-GiB protected reserve before any allocated submission.

No additional wait, inference, installation, model/service lifecycle action, process kill,
queue clear, GPU overlap, original timing/report overwrite or model load occurred.
