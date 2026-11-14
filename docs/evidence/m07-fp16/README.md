# SD-Turbo owned FP16 derivative: CPU preparation only

**Conversion and profile preparation succeeded. No model was initialised and no native/GPU
inference was authorised or performed. This does not complete M07 or prove runtime fit.**

Application base: `455840ab49b7fdff9bbeea49b4e3678fb909305d`.
The source is the same preserved official SD-Turbo checkpoint, not a new candidate or download.
The derivative has a new owned filename and is explicitly **not an official downloaded FP16 file**.

Profile/admission tests commit: `527c8cf589c6096991382169370b5f27123d6560`.

## Actual measured result

| Field | Measured value |
| --- | --- |
| Original filename | `sd_turbo.safetensors`. |
| Original size | 5214561328 bytes. |
| Original SHA-256, verified before and after | `3f067a1b943cf162f2b8f8588f6cf5824bd5b4c7d1d88d87164b9ca123616549`. |
| New owned filename | `sd_turbo-proscenium-fp16-1.safetensors`. |
| Actual derivative size | **2607364064 bytes**. |
| Actual derivative SHA-256 | `c1a0a0af943c0c197215fb7cd5bb8b72e342b98461e1c3719d8ba28300ba015e`. |
| Tensors / converted tensors | 1229 / 1229. The actual source contained only F32 tensor payloads. |
| Converted elements | 1303598228. |
| Original F32 / derivative F16 payload bytes | 5214392912 / 2607196456, exactly half the floating payload. |
| Other dtype payload bytes in actual checkpoint | 0. Integer/F16/empty-tensor preservation was exercised on labelled synthetic unit fixtures. |
| Derivative JSON header | 167600 bytes, padded to eight-byte alignment. |
| Converter chunk size / processed chunks | 4194304 bytes / 2249 tensor-bounded chunks. |
| Actual conversion wall time | **54.434780164 seconds**, including source hash verification, conversion, full read-back, output hash and final original hash check. |
| Actual converter peak RSS | **103510016 bytes**, measured from this exec's Linux VmHWM/VmRSS. |
| Minimum sampled host MemAvailable | **35388723200 bytes**, above protected 4294967296-byte headroom. |
| Environment | Existing Comfy Python 3.12.13, NumPy 2.5.3. No new installation. |
| Torch imported / model initialisations / GPU inferences | false / 0 / 0. |

Actual [conversion report](conversion-report.json), [write-once start receipt](conversion-started.json),
[official/runtime support](official-runtime-support.json), [preservation](preservation.json) and
[attribution notice](NOTICE.txt) are retained. The weights themselves stay outside Git at:

`/home/quintin/.cache/proscenium-development/image-generation-light/models/checkpoints/sd_turbo-proscenium-fp16-1.safetensors`

The source and derivative are both mode `0400`. Old Klein reports, timing/window receipts,
database bytes and the original F32 failed admission match the previously retained evidence.
No SQL Server, Blender, LM Studio, 8188 service, original checkpoint or source graph was modified.

## Supported precision, not an unmeasured inference claim

The pinned official model card at
[`stabilityai/sd-turbo@b261bac6fd2cf515557d5d0707481eafa0485ec2`](https://huggingface.co/stabilityai/sd-turbo/raw/b261bac6fd2cf515557d5d0707481eafa0485ec2/README.md)
documents both text-to-image and img2img using `torch_dtype=torch.float16, variant="fp16"`.
The local pinned card bytes match the accepted downloaded card, SHA-256
`2482ac53e71baf0624ede7aca74122ef60e442360535105cd57bcc9d7138c22e`.

The installed pinned Comfy source revision is `8ff6dc384ba5c410266b40e137799e049459d4f2`.
Its safetensors loader explicitly maps `F16` to `torch.float16`. Source inspection and
CPU serialization validation establish support for this precision representation.
**A future Comfy model load, runtime memory peak, GPU execution and output quality remain unproven.**

## Deterministic bounded conversion and validation

The converter is `tools/comfy/stream_fp16.py`, actual executed source SHA-256
`42e5311f48e85d455b3ccf929da517468a489a21126808d7e4c7fcfca845feb3`, from corrected commit `85e01e9`.
It uses standard binary I/O and NumPy only, with one-thread CPU-library environment limits.
It does not import torch, initialise a checkpoint/model, access a GPU or load a full state dict.

- Verify the original full SHA-256 before conversion and again after all validation.
- Parse a bounded JSON header, reject duplicate keys, invalid dtype/shape/counts,
  gaps/overlaps/aliases, incomplete bodies and uncovered trailing payload.
- Preserve tensor names, shapes and metadata. Recalculate contiguous offsets and F16
  byte counts deterministically. Copy recognised non-F32 dtype bytes exactly.
- Convert only F32 chunks with IEEE binary16 nearest/ties-even casting. Reject nonfinite
  input, values outside finite binary16 range or new nonfinite output. Never clamp.
- Check every chunk against `abs(error) <= 2^-11 * abs(source) + 2^-25`.
- Independently stream-read the entire derivative against the source, repeat finite/error
  checks, compare exact CPU-cast bytes for F32 and exact original bytes for other types.
- Validate complete output coverage, actual expected size, names/shapes/metadata and
  original inode/size/mtime/mode preservation before publishing a read-only new file.
- Source names/shapes digest: `fa608b5f798bc15dc4b824bde8b7f33b53c426ebf2e48d6ddbb7a0831567cc4e`.

Observed maximum absolute error was **0.0038881301879882812**. Maximum relative error for
normal-valued source elements was **0.0004880429477794046**, below `2^-11 = 0.00048828125`.
Exactly **231210** nonzero tiny source values rounded to zero within the stated absolute
subnormal bound; this is disclosed lossy rounding, not clamping or a claim of bit-exact weights.

The CLI has a write-once start marker and refuses any existing report/output/partial, so
an accidental rerun cannot silently perform another conversion. Invalid partial files are
retained for inspection and never published as valid derivatives.

### Pre-conversion guard instrumentation correction

An initial launch stopped in the guard **before source verification, the start marker or
any conversion**. Linux `getrusage().ru_maxrss` already reported 2382572 KiB even before
NumPy import, while `/proc/self/status` reported 29604 KiB for VmHWM/VmRSS. The inherited
launcher high-water counter is not this exec's resident peak.

The guard was corrected to check this process's VmHWM/VmRSS against the unchanged one-GiB
limit. Its actual conversion peak was 103510016 bytes. The inherited getrusage counter
2439753728 bytes is also retained in the report, not hidden. Only **one actual checkpoint
conversion** followed; five small synthetic unit cases are not model conversions.

## New explicit host-selected profile

`comfy-sd-turbo-img2img-fp16-1` is an additive revision selected only through private host
`provider_options.image_profiles`. It requires the exact new owned filename/hash. Its
model provenance is frozen on admission with:

- Original official repository/revision/filename/hash.
- Actual derivative filename/hash/bytes, explicit local-derivative status.
- Converter source hash, NumPy version, full conversion report hash, counts and precision limits.
- Original licence, unchanged conditioning method and calculated new-weight fit floor.

Conversion report SHA-256:
`b44b82b596d3c6caeed02ffd3cf432b11f53a401f5a270c0177e4b61368a824a`.

The fixed graph remains actual reference `LoadImage → VAEEncode → sampler latent`, prompt
conditioning, 512×512, four SDTurboScheduler steps, denoise 0.5 and CFG 1. The implementation
reuses the existing validated SD-Turbo graph binder and image plugin; only the sealed
checkpoint identity/save prefix differ. There is no second inference pipeline, ledger,
job service, bridge, public schema, UI or Copilot modification.

Original F32 `comfy-sd-turbo-img2img-1`, Klein `comfy-image-1`, legacy missing-plugin image
admissions, Hunyuan and VLM paths remain unchanged. No old frozen job is reinterpreted.

## Same formula, measured new-file floor

```text
actual derivative bytes + 8 GiB = 2607364064 + 8589934592 = 11197298656 bytes
max(12 GiB, 11197298656) = 12884901888 bytes
```

The new floor is **12884901888 bytes (12 GiB)**. The original F32 floor remains
13804495920 bytes and the original Klein floor remains 20427268612 bytes. This arithmetic
does not lower an old job's guard and is not a GPU fit measurement. A future separate
allocation must assess actual post-start/pre-POST resources for the new profile.

## Exact executed CPU command and affected checks

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 /home/quintin/.local/opt/ComfyUI/.venv/bin/python -I -B tools/comfy/stream_fp16.py --convert-owned-sd-turbo
```

- Five synthetic CPU converter tests passed, including overflow/nonfinite rejection,
  malformed coverage, byte-preserved other dtypes/metadata, deterministic repeated tiny
  fixtures, rounding checks and actual-process RSS guarding.
- **90 affected provider integration tests passed**. FP16 admission freezes actual model
  hash/conversion provenance, while existing F32/Klein/Hunyuan/VLM cases remain passing.
- Ruff check/format passed on the five maintained changed Python files. Provider mypy
  passed on 14 source files. Converter mypy passed using Python 3.12 and the installed
  Comfy interpreter's NumPy 2.5.3 stubs, without adding a root/global dependency.
- Specification validation passed: 119 definitions, 53 operations and 20 semantic oracles.
  No public schemas, generated bindings, jobs, DB, UI, lifecycle or Copilot files changed.
- No full checkpoint conversion rerun, model load, inference, runtime fit or M07 positive
  journey is implied by these preparation tests.

## Licence and changes

Powered by Stability AI. This Stability AI Model is licensed under the Stability AI
Community License, Copyright © Stability AI Ltd. All Rights Reserved. The original
[pinned licence](../m07-lightweight/model-licence.txt) applies to this owned precision
derivative. Proscenium changed F32 tensor storage to F16 with the disclosed bounds; no
training or other candidate was used. This is not a declaration of cleared commercial
deployment rights.
