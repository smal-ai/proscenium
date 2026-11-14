# W07 bounded integration follow-up

**Date:** 14 November 2026, SAST. **Accepted application base:**
`029e56aabe8b79c830cc2abaad1c5acdd36e1b48`. This follows accepted W07 `a9e15ac`.
Only provider/tool/skill/provider-test/W07 documentation paths are changed. There are no
shared schema, migration, job/auth service, root router/composition or Copilot edits.

Tested implementation commit: `279e9ca582499ddf3053f979bd4c1e18a5832a1b`.
The original background shell was `sh_1da008b32001ztFpWYnGCGYYCF`. Its actual completion
notification reported exit status 1 and the unchanged planned job. The proof was not
polled/re-run in response to the later implementation-commit instruction.

## Accepted seams exercised

- Actual W02 `Artifacts.promote` outside the mutation transaction, then
  `Artifacts.put(tx, artifact)` inside an exact-worker idempotent receipt transaction.
  Generic tool registration remains restricted to its original tool/staging authority.
- Original worker project/job/current attempt/input digest and operation allowlist are
  checked. Original matching operation/body/binding replays before staging/status checks.
  Unsafe staging roots, symlinks, secret paths, wrong hashes and missing derived inputs
  fail. Dimensions, transformations and pinned source version survive promotion.
- Missing generation policies read only W01's frozen
  `copilot.effective[work.originating_request_id].generation_attempt_limit`. They never
  adopt an edited application default or a tool's claimed limit.
- Actual W02 Git-backed publications and immutable authored source resolution establish
  storyboard/sequence target identities. Alias changes and later published versions share
  the same task/target ledger. Distinct real slots remain distinct. An originating captured
  item version wins over a subsequently published head.
- With empty settings, existing explicit bindings still work. Otherwise only an actual
  item or the originating task's single unstructured concept identity is accepted.

Commands used for targeted verification:

```bash
uv run ruff check apps/backend/src/proscenium/providers tools/comfy tests/integration/providers tests/native/providers
uv run ruff format --check apps/backend/src/proscenium/providers tools/comfy tests/integration/providers tests/native/providers
uv run mypy apps/backend/src/proscenium/providers tools/comfy tests/integration/providers tests/native/providers
uv run pytest -q tests/integration/providers
uv run python docs/validate_spec.py
```

The provider suite covers real W02 bridge, published slot, cross-version/alias,
item-context and sequence-entry cases. Final verification results are below. The existing
Starlette TestClient/httpx deprecation warning remains unrelated to provider execution.

| Targeted check | Result |
| --- | --- |
| Ruff check and formatting, all owned Python paths | Passed, 17 files formatted. |
| mypy, all owned Python paths | Passed, 16 source files checked. |
| `uv run pytest -q tests/integration/providers` | **57 passed**. |
| `uv run python docs/validate_spec.py` | Passed. 119 definitions, 53 operations, 20 semantic oracles and valid local links. |
| Retained native receipt verification command below | **1 passed, 1 skipped**. Passed: real memory-blocker/reference receipt assertions. Skipped: generated-output PNG assertions because no inference ran. This is not a GPU execution pass. |

## Bounded live proof procedure

The explicit invocation is:

```bash
uv run python tools/comfy/prove_generation.py --execute
```

This program is not a pytest-discovered inference test. The durable allocation permits
only `w07-live-initial`, then `w07-live-correction` **only after an actual initial error**.
Matching reruns retain job/client/prompt IDs. A durable per-job start time bounds the
wait/reconciliation period to 600 seconds. There is no automatic correction or blind POST
retry, and no global cancellation or service/model lifecycle path.

All project, SQLite, input/reference/provenance and output bytes stay under the authorised
home scratch:

`/home/quintin/.cache/proscenium-development/providers-w07-20261114/`

The program composes actual F00 services, W02 catalogue/artefacts and W05 reviews. Its
work/invocation/frozen owner allowance are explicitly development-owned fixture authority,
not evidence of a native Copilot turn. Actual inference and output promotion use the real
Comfy endpoint, production adapter, canonical standalone target, F00 ledger/resource claim
and exact-worker W02 bridge. W05 is installed but no review or VLM call is performed.

Allocation and resource checks:

- Exactly the existing `http://127.0.0.1:8188` service.
- Exactly 64×64, four steps, seed 42, one generated 64×64 conditioning reference.
- Existing `flux-2-klein-4b.safetensors` (7751105712 bytes),
  `qwen_3_4b.safetensors` (8044982048 bytes), `flux2-vae.safetensors` (336213556 bytes).
- Before submission: external queue must be empty. Service-reported free device memory
  must exceed those 16132301316 weight bytes plus 2 GiB. `/proc/meminfo` MemAvailable must
  exceed weight bytes plus 4 GiB. These are conservative proof admission checks, not a
  general model-fit guarantee.
- Actual F00 `heavy-gpu` claim is acquired by the adapter. Its isolated development registry
  is advisory for this proof, not a claim over unrelated model services. External queue and
  memory observations are independently rechecked. Busy/insufficient memory waits or blocks.
- The source and derived conditioning images are receipted. LoadImage feeds VAEEncode,
  ReferenceLatent and positive CFGGuider. Outputs remain separate immutable PNG artefacts.
- No model install/load/unload, service start/stop, queue clear, VLM call or reference-asset
  copy is performed. Comfy's normal execution may load its already-installed graph models.

The report format retains exact command/frozen inputs, any submitted graph/hash and
prompt/client identity, device/runtime observations, module source hashes, budget states
and any actual output receipts. It does not invent a prompt ID or PNG when submission is
blocked.

## Measured result: host memory blocked inference

**No Comfy inference was submitted. No correction was launched. No PNG generation is
claimed.** The 600-second resource wait expired with the original job still planned.
Although Comfy's queue was empty and its reported device memory passed admission, host
MemAvailable never met the configured proof headroom. It was not safe to treat reported
device memory as independent physical RAM on this workstation.

| Observation | Actual value |
| --- | --- |
| First resource observation | `2026-11-14T14:20:31.531062+02:00`. |
| Last resource observation | `2026-11-14T14:30:31.121765+02:00`. |
| Final report timestamp | `2026-11-14T14:30:31.527283+02:00`. |
| Resource observations | 300, within the original 600-second allocation. |
| Required host MemAvailable | 20427268612 bytes, model weights plus 4 GiB. |
| Observed host MemAvailable minimum / maximum | 16254963712 / 19973545984 bytes. |
| First / last MemAvailable | 18820550656 / 19625476096 bytes. |
| Service-reported `vram_free` / `vram_total` | 37570478080 / 68719476736 bytes. |
| Queue at every observation | Zero running, zero pending. |
| Job / attempt | `w07-live-initial` / `attempt-70a06bf9-1fbb-4188-8162-bddc030e7cd4`. |
| Job state / outbox state | `planned` / `pending`. No external dispatch occurred. |
| Canonical target / task | `w07-live-task` / `w07-live-task`. |
| Budget | One reserved, zero consumed. No second reservation or job. |
| Provider execution marker / prompt ID / client ID | None. No submission was attempted. |
| Held resource claims after the proof | None. No GPU claim was needed because pre-submit host admission never passed. |
| Generated reference | 64×64 PNG, 180 bytes, W02 ready artefact `w07-live-reference`. |
| Reference SHA-256 | `8bb0701d5c265584da05366f7e9f57779d5293138c70d111cf40797a29e90656`. |
| Job input digest | `ab6c1b4bf2ab8d097f0b8550c08a3a60faebb20d78a909e47a88a19fdef04a8e`. |
| Provider output artefacts | None. |

ComfyUI reported `0.37.4`, frontend `1.52.7`, Python `3.12.13`, PyTorch
`2.13.0+rocm10.0.0`, device `cuda:0 AMD Radeon 8060S Graphics : native`. These are measured
service identities, not evidence that the allocated graph ran.

The exact retained frozen 17-node conditioning graph subsequently passed **read-only**
actual `/object_info` preflight at `2026-11-14T14:31:17.481927+02:00`:

- Frozen graph SHA-256: `ea9080a80273fa04eb7788c5c30053481c21ce960fe689190e7f17b143c5a253`.
- Catalogue HTTP response SHA-256: `edb933612288d372c95239b4e1510ea0d42236c0c3b477146a979d99c9f352b7`.
- Reference upload to Comfy: **not performed**. The ready project reference is retained
  through real W02 storage. The actual Comfy input receipt and generated output PNG remain
  unproven until a separately bounded allocation can pass host-memory admission.

Retained outside Git:

- `w07-live-initial-report.json`, SHA-256
  `19dd5cc635dc9ea665910e5336ebe787f192573a521bcda71aefa51e316647ac`.
- `w07-live-readonly-graphcheck.json`, SHA-256
  `9b63ef2a624fb006e4e676477a5dc8e3b8580eba06c428c429b0de7327fce64a`.
- Original project DB, immutable graph/prompt/reference artefacts and timing record under
  the home scratch above. There is no generated PNG to retain or assess.

Repeating the proof command after expiry reads the original report and exits without
opening services, changing retained evidence or extending the deadline. The correction
path requires a known failed initial inference, so this planned resource-blocked job
cannot be relabelled as authority for a fresh correction. No memory threshold was reduced,
unrelated service/model was altered or fresh job was submitted to evade the blocker.

Receipt verification, which never calls a service or submits inference:

```bash
PROSCENIUM_W07_GENERATION_RECEIPT=/home/quintin/.cache/proscenium-development/providers-w07-20261114/w07-live-initial-report.json uv run pytest -q tests/native/providers/test_generation_receipt.py
```

The intended successful proof would establish execution, actual conditioning input/graph
receipts and an immutable decoded PNG. **That proof remains blocked.** Neither this resource
guard result nor schema preflight establishes visual quality, similarity, full
browser/native-SDK integration, M07 completion or VLM capability.
