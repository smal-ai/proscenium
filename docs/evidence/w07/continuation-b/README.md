# Original W07 job: continuation window b

**Result: host-memory blocked before inference.** The original job is still planned,
not failed, with its exact original attempt/input/task and reserved budget identity.
No prompt/client ID was created. No original report or deadline was reset.

- Application base: `069a85497cf67117eb66ca206b3b4a208e9c7864`.
- Executed continuation code: `3f2663584b3f39095569f75db787a30aebe21e1e`.
- Original background shell: `sh_1da9711db0017BhqnTL3H2kd6w`, completed with exit 1.
- Private append-only directory:
  `/home/quintin/.cache/proscenium-development/providers-w07-20261114/continuations/w07-continuation-b/`.

## Actual records

| Record | Purpose |
| --- | --- |
| [Window receipt](window.json) | New explicit allocation and complete before snapshot, original hashes, frozen inputs and identity. |
| [Final report](report.json) | Complete after snapshot, unchanged original hashes, no execution marker or output. |
| [Observations](observations.jsonl) | All 300 actual queue/device/host observations, without unrelated prompt content. |
| [Preservation](preservation.json) | Protected Qwen/profile unchanged, Gemma unloaded, no resource claims. |
| [Manifest](manifest.json) | SHA-256 and length of exported raw receipts. |

## Measured resource result

| Field | Observation |
| --- | --- |
| Window started | `2026-11-14T17:04:56.481586+02:00`. |
| Last resource observation | `2026-11-14T17:14:55.931606+02:00`. |
| Admission/observation deadline | 600 seconds. Every resource observation occurred before it. |
| Final report timestamp | `2026-11-14T17:14:56.493508+02:00`. |
| Wall time including local final snapshot/report sealing | 600.011922 seconds. The extra approximately 12 ms was bookkeeping, not provider execution. |
| Required host MemAvailable | **20427268612 bytes**, unchanged original weights-plus-four-GiB threshold. |
| Actual minimum / maximum MemAvailable | **11452526592 / 16763625472 bytes**. |
| Shortfall even at observed peak | **3663643140 bytes**. |
| External queue, all 300 samples | Zero running, zero pending. |
| Service-reported `vram_free` / `vram_total` | 37585158144 / 68719476736 bytes. |
| Service-reported torch reserved/free, all samples | 2097152 / 2097152 bytes. No measured loaded Klein residency justifies a hot-reuse assumption. |
| Device | `cuda:0 AMD Radeon 8060S Graphics : native`. |
| Initial read-only service runtime | ComfyUI 0.37.4, Python 3.12.13, PyTorch 2.13.0+rocm10.0.0. |

The exact retained 17-node, 64×64, four-step graph passed actual `/object_info` preflight
before the new window receipt. No model filename, seed, prompt, reference or graph input
was changed. Service-reported device memory did not substitute for the failed host-RAM
check. No threshold was lowered and no model was unloaded to manufacture room.

## Exact original identities and preserved bytes

- Project `w07-live-proof`, job `w07-live-initial`.
- Attempt `attempt-70a06bf9-1fbb-4188-8162-bddc030e7cd4`, number 1.
- Work `w07-live-work`, task/target `w07-live-task`.
- Input digest `ab6c1b4bf2ab8d097f0b8550c08a3a60faebb20d78a909e47a88a19fdef04a8e`.
- Job state remains `planned`, state revision 0. Original outbox remains `pending`.
- Exactly one ledger row: original reservation `w07-live-initial`, still `reserved`.
- Provider endpoint stays frozen at `http://127.0.0.1:8188`.
- Original seed 42, original installed Klein 4B/Qwen 3 4B/Flux2 VAE model filenames.

The old files have identical before/after SHA-256:

| Original file | SHA-256 |
| --- | --- |
| `w07-live-initial-report.json` | `19dd5cc635dc9ea665910e5336ebe787f192573a521bcda71aefa51e316647ac` |
| `w07-live-initial-timing.json` | `7e04338414b5af035acab46389b26a7bd3e113f326fdd1778631d684341c67fe` |

The new CLI path does not call `submit`/`admit`, create work, change the invocation epoch,
reset a reservation or assign this original harness job to native M07 work. It reads the
current job, inputs and execution state first. A known prompt/client is reconciled; there
was no accepted provider identity in this run to query as owned history.

## Scope and next dependency

**Zero actual inference executions and zero provider POSTs.** The known-ACK-loss option
was enabled but never reached transport, so neither live HTTP ACK loss nor native RPC
ACK loss is proven here. Its write-before-network/one-POST guard has offline coverage only.
No GPU claim was acquired because host admission never passed. No claims remain.

No new M07 native project, browser, cloud root, generation, selection or correction was
started after this blocker. No actual output PNG or completed M07 journey is claimed.
Independent ComfyUI, original Qwen and saved LM Studio profile were preserved. No VLM,
mesh, Blender, Unity, installation, service restart/stop/global interrupt or queue-clear
operation was performed.

Another execution window needs separately recorded coordinator authority and a distinct
append-only name. Repeating this settled name only returns its retained receipt and cannot
extend the deadline or send inference. The immediate prerequisite is enough measured
host RAM for the original cold submission, not a model-parameter search.
