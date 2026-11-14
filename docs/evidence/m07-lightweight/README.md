# M07 lightweight Comfy image family: preparation

**Preparation only. No SD-Turbo inference or M07 positive journey is claimed yet.**
The selected single candidate is official public [SD-Turbo](model-source.json), pinned to
`b261bac6fd2cf515557d5d0707481eafa0485ec2`, filename `sd_turbo.safetensors`, expected
5214561328 bytes and SHA-256
`3f067a1b943cf162f2b8f8588f6cf5824bd5b4c7d1d88d87164b9ca123616549`.
The model API reported public/not gated and its pinned model card explicitly documents
img2img. No token, purchase or second candidate is used.

Validated profile/source commit: `fc65e72802691d2998d0c41bc2881ac2c6d3dbd1`.
The original download completed successfully before that commit. No temporary Comfy
process, native SDK request, inference job or image selection has been started yet.

## Code and real isolation plan

- New additive plugin revision: `comfy-sd-turbo-img2img-1`.
- Original `comfy-image-1` remains the default. Frozen/missing-plugin legacy image jobs
  still resolve to that exact original revision. Hunyuan and VLM implementation is unchanged.
- Host selection only: private
  `provider_options.image_profiles = {"m07-light-comfy": "comfy-sd-turbo-img2img-1"}`.
  Native tool JSON cannot select a revision, Python implementation or relaxed graph.
- One 512×512 reference, no implicit resize, actual `LoadImage → VAEEncode →
  SamplerCustomAdvanced.latent_image`. CLIP positive text conditioning, CFG 1, Euler
  ancestral, `SDTurboScheduler` four steps and denoise 0.5. This is **img2img**, never
  described as FLUX ReferenceLatent conditioning.
- Same production ProviderService admission, W01 frozen owner allowance/canonical target,
  F00 ledger/outbox/resource claim, prompt-ID recovery and exact-worker W02 bridge.
  No new inference CLI, ledger, DTO, schema, shared job reducer or router exists.
- Profile revision, pinned expected model hash, conditioning method and input dimensions
  are frozen in job inputs. Real metadata/output registration remains the shared pipeline.

`tools/comfy/sd_turbo_instance.py` describes, but does not start, an owned server on
`127.0.0.1:18589`, with pinned existing code revision
`8ff6dc384ba5c410266b40e137799e049459d4f2`. Its pure planner checked tracked source clean,
`extra_model_paths.yaml` absent, canonical private model storage and free port. Every
mutable base/user/input/output/temp/cache/database path is inside the new run root.
Custom/API nodes and auto-launch are disabled; environment caches are isolated/offline and
CPU libraries bounded to four threads. Independent 8188 is never reconfigured.

## Retained download and exact next execution plan

Original download shell: **`sh_1dafa8fd1001VgIjzbom4X6rhz`**.
It streamed only the pinned public file into isolated disk storage and completed with
verified full bytes/hash. [Actual metadata](verified-weights.json) confirms F32 payloads;
no guessed FP16 filename or alternate model was downloaded. The checkpoint is read-only.
The [pinned model card](official-model-card.txt) and [full licence](model-licence.txt) are
retained. Signed redirect URLs were never logged or saved.
Those two third-party files retain upstream bytes, including upstream trailing whitespace;
maintained-source whitespace checks exclude only those verbatim snapshots.

1. **Completed:** original download consumed, actual bytes/hash verified, official
   licence/model card retained. Do not fetch a second candidate or blindly re-download.
2. Record unchanged original W07 job/attempt/task/reservation/report/database identity and
   protected Qwen/profile baseline. No operation uses that old admission for this profile.
   [Read-only original snapshot](original-klein-preservation.json) already confirms its
   job, frozen inputs, ledger and main DB bytes are unchanged. Recheck after real execution.
3. Use a fresh run directory:
   `/home/quintin/.cache/proscenium-development/image-generation-light/runs/m07-lightweight-20261114/`.
   Check host MemAvailable against **13804495920 bytes**, the larger of 12 GiB and actual
   checkpoint bytes plus 8 GiB. If it fails, block immediately, not another RAM wait.
4. Start only the planned owned Comfy child, at most 1800 seconds, and sample host memory.
   Stop only that PID/start-verified process if available RAM falls below 5 GiB, preserving
   four-GiB headroom. Recheck the cold floor after startup and before a provider POST.
5. Compose the actual production application/lifecycle and compiled 1920×1080 owner browser
   with one native host-selected image provider at 18589. Set up a labelled two-frame
   published storyboard and actual owner reference ingestion via W02. The unrelated frame
   is explicitly separate and must receive no generation job.
6. First real native `gpt-6.1-sol` Queue calls `submit_generation` once using its actual W01
   `generation_task_id`, exact storyboard `target_slot`, graph/prompt/reference artefact IDs
   and planned job ID `m07-light-initial`. No harness Caller impersonates the native tool.
   Wait/read/reconcile that admitted identity up to 600 seconds without resubmission.
7. Display the real first PNG/provenance, choose it through the actual owner selection UI,
   and retain its exact image/selection bytes. A new explicit owner correction Queue calls
   the real native tool once for planned job ID `m07-light-correction`, same canonical frame,
   actual first image as img2img reference, separately registered correction graph/prompt.
   Distinct explicit Queue task identity will be reported honestly rather than called a
   same-task retry. The old selected image/unrelated frame remain available and unchanged.
8. Capture technical completion without automatic selection, then any explicit owner choice,
   exact budget/endpoint/seed/model/conditioning/derivative/output hashes, image reuse reads,
   source preservation and precise native callback/job/attempt IDs. At most two actual
   inferences and three cloud roots; no children or VLM/mesh/Blender/Unity work.
9. After own jobs settle, stop only the owned temporary process and retain all receipts.
   A guard/transport failure keeps original admitted provider identities and budget
   uncertainty. No 8188 interrupt/free/queue clear/unload is permitted.

Known provider ACK-loss reconciliation may be observed only if one actual accepted response
can be lost without an extra POST. Otherwise it remains unproven, not simulated into a pass.

## Validation to date

- **81 existing/new provider integration tests passed**. Includes original Klein,
  Hunyuan/image-to-3D, VLM and new image-family checks. New profile HTTP outputs in tests
  are explicitly synthetic and are not native proof.
- Ruff and mypy passed. The isolated plan's source/paths/port checks passed without execution.
- Actual frontend TypeScript/Vite production build passed, with existing Lucide/bundle warnings.
- This is not a GPU execution, image-quality result, Full-HD generation journey or M07 pass.

## Licence and attribution

Powered by Stability AI. This Stability AI Model is licensed under the Stability AI
Community License, Copyright © Stability AI Ltd. All Rights Reserved. The inspected pinned
licence permits research/non-commercial evaluation/testing subject to its terms, and has
separate commercial registration/revenue conditions. This document does not assert cleared
commercial deployment rights. Full source licence/model-card URLs are recorded in the
candidate metadata; checkpoint bytes are not committed to Git.
