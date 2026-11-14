# W07 evidence: local providers

The original evidence below is historical. The bounded integration follow-up against
`029e56aabe8b79c830cc2abaad1c5acdd36e1b48` is recorded separately in
[integration and live generation evidence](integration-20261114.md). It adds accepted W02
promotion, lazy frozen-allowance target identity resolution and a separately allocated
Comfy proof. No VLM load/call was allocated for that follow-up.

**Date:** 14 November 2026, SAST. **Application base:**
`76d845133c44c8759f50299160322b717e60d00a`. **Accepted review dependency:**
`9945df9059ed0f659b7acfdde16ce0574731cdad`.

This evidence distinguishes offline synthetic-provider execution from live service
discovery. No Comfy inference, upload, mesh generation, model load/unload or service
lifecycle change was performed. No VLM inference was performed because no vision-capable
model was already loaded. Existing services, queues, project assets and native sessions
were not modified.

## Runtime and commands

Linux, CPython 3.12.13, httpx 0.28.1, Pillow 12.3.0, pytest 8.4.2, Ruff 0.17.5,
mypy 1.20.2. Dependencies came from the accepted lock using `make bootstrap`.
No manifest, lock, shared schema, migration, authentication, job implementation or root
composition file was changed by W07.

Final verification commands:

```bash
make bootstrap
uv run ruff format apps/backend/src/proscenium/providers tools/comfy tests/integration/providers tests/native/providers
uv run ruff check apps/backend/src/proscenium/providers tools/comfy tests/integration/providers tests/native/providers
uv run ruff format --check apps/backend/src/proscenium/providers tools/comfy tests/integration/providers tests/native/providers
uv run mypy apps/backend/src/proscenium/providers tools/comfy tests/integration/providers tests/native/providers
uv run pytest -q -m 'not native' tests/integration tests/unit tests/contracts
PROSCENIUM_W07_COMFY_ENDPOINT=http://127.0.0.1:8188 PROSCENIUM_W07_VLM_ENDPOINT=http://127.0.0.1:1234 uv run pytest -q -s tests/native/providers
```

Final results:

| Check | Result |
| --- | --- |
| `make bootstrap` | Passed using the frozen dependencies. npm audit reported zero vulnerabilities. |
| Ruff check and format check, owned Python paths | Passed. 12 files already formatted. |
| mypy, owned Python paths | Passed. 11 source files checked. |
| Combined offline integration/unit/contract command above | **169 passed**. Includes 37 W07 provider cases and 23 accepted W05 store cases. |
| Explicit native provider command above | **1 passed, 1 skipped**. Live Comfy schema preflight passed. VLM skipped because no vision-capable model was loaded. |

Starlette emits the foundation's existing httpx TestClient deprecation warning. The
warning is not treated as native/model evidence.

## Measured live discovery

At `2026-11-14T13:35:55.572839+02:00`:

| Request | Measured result |
| --- | --- |
| `GET http://127.0.0.1:1234/v1/models` | HTTP 200. Four LLM IDs plus Nomic embedding inventory. |
| `GET http://127.0.0.1:1234/api/v1/models` | HTTP 200. Exactly one loaded LLM instance, explicitly non-vision. |
| `GET http://127.0.0.1:8188/system_stats` | HTTP 200. ComfyUI was now running independently, unlike the earlier parent observation of connection refusal. |
| `GET http://127.0.0.1:8188/queue` | HTTP 200. Zero running, zero pending at inspection. No queue mutation. |
| `GET http://127.0.0.1:8188/object_info` | HTTP 200. Actual required Klein/reference node and model choices present. |

LM Studio's native inventory reported:

| Exact model key | Vision capability | Loaded instance |
| --- | --- | --- |
| `qwen3.8-27b-uncensored` | false | `qwen3.8-27b-uncensored`, context length 131072. |
| `gemma-4-26b-a4b-it-qat` | true | None. |
| `qwen/qwen3.8-27b` | true | None. |
| `google/gemma-4-e4b` | true | None. |
| `text-embedding-nomic-embed-text-v1.5` | Not a VLM. | None. |

Therefore `/v1/models` availability was **not** treated as evidence that an image-capable
model was loaded. The bounded VLM native test skipped before inference. No attempt was
made to load a different model, use a text model as a visual reviewer or alter independent
model settings.

ComfyUI reported:

- ComfyUI `0.37.4`, frontend `1.52.7`, workflow templates `0.11.69`.
- Python `3.12.13`, PyTorch `2.13.0+rocm10.0.0`.
- Device: `cuda:0 AMD Radeon 8060S Graphics : native`.
- At discovery: device `vram_total=68719476736`, `vram_free=37570478080` bytes.
  These are service-reported observations, not a model-fit or available-allocation claim.
- Read-only installed revision at the reference-documented installation path
  `/home/quintin/.local/opt/ComfyUI`: `8ff6dc384ba5c410266b40e137799e049459d4f2`.
- Exact available model filenames used for preflight:
  `flux-2-klein-4b.safetensors`, `qwen_3_4b.safetensors`, `flux2-vae.safetensors`.
- Required `LoadImage`, `VAEEncode`, `ReferenceLatent`, `EmptyFlux2LatentImage`,
  `Flux2Scheduler`, `CFGGuider` and `SamplerCustomAdvanced` nodes were present.
- The actual KSamplerSelect schema uses `COMBO` with `options`, rather than the older
  list-only choice shape. Live inspection exposed this distinction. The adapter and
  offline double now validate both actual schema forms.
- Installed `server.py:1120-1134` was inspected read-only. It places the submitted
  `client_id` into queue tuple `extra_data`, supporting the adapter's client-ID recovery
  path. No code from that installation is imported by the application.

Both the 13-node text graph and 17-node reference-conditioned graph were preflighted
against that live catalogue using 64×64 dimensions and seed 42. A fixture reference token
was used for schema validation only. No bytes were uploaded and no `/prompt` request was
made. Service preflight does not prove execution, conditioning fidelity or output quality.

## Offline coverage

`tests/integration/providers/` uses actual F00 SQLite transactions, job attempts, frozen
inputs, outbox records, generation ledger, gate and shared resource claims. It also uses
the **real accepted W05 review store**, not a review-store stub. HTTP providers and artefact
registration are explicitly synthetic. The registrar double validates exact
`ArtifactRegister`/`Artifact` contracts and writes only disposable fixture stores.

| Acceptance | Demonstrated offline scope | Remaining cumulative proof |
| --- | --- | --- |
| C03 | Accepted-before-timeout and unknown acceptance, same endpoint/client/prompt reconciliation, one POST, one reservation, existing output download. | Live provider timeout/recovery under an allocated inference. |
| C12 | Initial/correction jobs across child identities, durable reservations, fourth rejection, renamed target/task denial, retry bypass rejection, frozen defaults, owner-only recorded extension without reset. | Parent Queue/Steer/Resume must bind original task policy in its real runtime path. |
| C17 | Two distinct endpoints through preflight/queue/memory/upload/submit/history/view, exact saved conditioned graph and derivative provenance, no old creative defaults or service lifecycle calls. | Actual generated-image visual/reference fidelity. |
| C18 | Ticket-only native responses, host-stamped private findings, three findings with one owner-amended exact revision/target submitted once through the real W05 transaction seam. | W01 native forwarding and complete owner-browser journey. |
| C27 | Owner review has nullable work/original owner operation and one job even while unrelated work is active, exact-attempt worker completion, visible job/cancellation refusal. | W08 Unity and application-wide settlement/browser journey. |

Additional negative/race checks cover malformed/truncated/fabricated findings, measured
evidence without explicit telemetry, unloaded/text-only VLM rejection before POST,
service unavailable, queue busy, resource contention, concurrent idempotent admission,
waiting-dispatch replacement races, reset gates, explicit Resume, immutable staged output,
missing review/artefact capabilities and unsupported global cancellation.

## Safe next live proof allocation

- **Comfy:** the independently running service already has viable Klein/reference schemas
  and filenames. A separately authorised test can use one disposable 64×64 reference PNG,
  four steps and one saved output, after acquiring `heavy-gpu`, rechecking queue/memory and
  using the parent-composed W02 registrar. No installation/service change is necessary.
  Generation was not authorised for this W07 allocation, so it was not run.
- **VLM:** an owner-managed vision instance must already be loaded before this allocation
  can execute. Once available, the native test sends one generated 64×64 PNG, at most 512
  output tokens and a 120-second HTTP timeout. It has no load/unload path. Current loaded
  non-vision Qwen does not meet that prerequisite.
- **Unimplemented:** image-to-3D pending W09/E04, provider-specific own-job cancellation,
  operator resolution of a crash before any submission marker, visual-quality acceptance
  and the full composed browser/native runtime journeys.
