# M07 FP16 actual native generation, selection and provider recovery

**Actual generation/selection/recovery evidence delivered. Coordinator acceptance pending.**
The requested cobalt-blue correction was **not achieved**. No M07 milestone completion is claimed.

## Allocation and identities

- Run: `m07-fp16-execution-20261114`, 14 November 2026, approximately 20:13–20:21 SAST.
- Product base: `011ab92d7c0f2a85ce61ea0235ad305d98b317fe`.
- Preflighted, committed execution harness: `970040b8a22a3298b653924b2ce033e1b8ffba86`.
- Retained-evidence tests/exporter and fixture corrections: `b513b1e`.
- Project: `project-2d8c0aea-e3de-41cf-9c23-1d0108fd8bb8`. Disposable source and database only.
- Canonical target: `storyboard-5372b6808ed2744c394b0d48fe415889d2348e83d27d82c3525b81fc7010460a`.
- Slot: `m07-fp16-storyboard` / `m07-fp16-plan-v1`, `shot-mug`, `frame-mug`.
- SDK 1.0.34, direct CLI 1.0.112, native lead `gpt-6.1-sol`, normal existing GitHub CLI authentication.
- Comfy revision `8ff6dc384ba5c410266b40e137799e049459d4f2`, one owned child on `127.0.0.1:18589`.
- Exact FP16 derivative: `sd_turbo-proscenium-fp16-1.safetensors`, 2607364064 bytes,
  SHA-256 `c1a0a0af943c0c197215fb7cd5bb8b72e342b98461e1c3719d8ba28300ba015e`.
  Conversion/source provenance remains in the [accepted parent evidence](../README.md).
- Profile `comfy-sd-turbo-img2img-fp16-1`. Actual graph uses reference image → VAE latent → sampler,
  positive prompt conditioning, four steps and denoise 0.5. Both PNGs are 512×512, seeds 42 and 43.

| Identity | Initial | Correction |
| --- | --- | --- |
| Job | `m07-fp16-initial` | `m07-fp16-correction` |
| Attempt | `attempt-ad6f9d52-fc3c-4b21-93bb-20ce25cb78e7` | `attempt-4255e36f-b8c4-4983-b37f-d12c67d2488e` |
| Provider prompt | `79d8948d-8ed9-4975-ac2c-a628b957a27b` | `9cbb1228-e32a-4c81-a587-06dbd66c20ab` |
| Human generation task | `task-278fabc477684504bd316016216ea8f3` | `task-64f49649d9004a15a32ba790eb6b81e7` |
| Owner request | `43349d0a-832c-4c18-a0d1-f6e66054d010` | `c4f6a756-1636-43a6-81d7-c20a20eed104` |
| Input digest | `52f82d065512e98b2c7602dbd3e2ec11f7f0fa53e923fdaafd7e422d87fcb3a0` | `861e3135e42e6eabb15c809eeb5f5a68f88ae3fd3fc714f99248e762ff3444f8` |
| PNG SHA-256 | `07673e7a7cea364e56b52bb4bc255722678e0cbf20b1b2c714e819c5ab84806d` | `5c84752669807be5857f90ff13b31d6f0766b4e2313bea7030a5be793214e8df` |
| PNG bytes | 84651 | 97386 |

## Actual acceptance criteria

| Criterion | Observed result and evidence |
| --- | --- |
| Native generation authority | PASS. Two actual owner UI Queues, each followed by exactly one native `submit_generation`. Host setup published labelled source/reference fixtures but created no generation job. `native-delivery.json`, `generation-tool-results.json`, `public-owner-commands.json`. |
| Reference conditioning and frozen inputs | PASS. Initial uses the labelled owner reference. Correction uses the exact initial generated PNG. Prompt, graph, model, input and endpoint identities appear in `post-cleanup.json`, `prompt-request-*.json` and both provenance files. |
| Actual visible output | PASS. Compiled production frontend, one continuous browser/profile, 1920×1080 at DPR 1. Both real output PNGs displayed with actual provenance. `browser-select-initial.png`, `browser-select-correction.png`, `public-collect.json`. |
| Explicit owner selection | PASS. Initial selection revision 1, correction supersession revision 2 on resource `deb3502b-7283-44ea-899d-ccc28377e459`. Technical correction completion did not replace the initial choice. |
| Preservation and reuse | PASS. Both generated PNGs retained. Unrelated reference and selection remain unchanged at revision 1. Reload and content-hash reads caused zero extra inference POSTs. `public-reuse.json`. |
| Provider ACK-loss recovery, C03/M12 | PASS within the exact tested boundary below. One initial POST, same attempt/input/client throughout, real history reconciliation, no automatic resubmit. Native RPC ACK loss and quiet application restart are NOT TESTED. |
| Actual C12 provenance | PASS for two separate tasks, each frozen at default policy 3 and consuming one reservation against the same target. No native children or actual three-consumed-default exhaustion were allocated. Accepted offline tests remain the only evidence for those cases. |
| Requested semantic correction | FAIL. Prompt requested cobalt blue, but both observed images remain red. Shape/detail and hashes differ. No reroll or extra inference was spent. |
| Cleanup and protection | PASS with disclosed fixture deviation. All owned host/SDK/Comfy processes exited, zero native children, no resource claims remain. Original Klein fixture and F32 failed admission files are byte-preserved. |

## Real response-loss boundary

The test-only transport captured and durably recorded the real successful initial `/prompt`
response, including prompt ID and response hash. It waited until genuine completed history
existed, then withheld that response by raising `httpx.ReadTimeout`. It never supplied the
captured ID to the production adapter or rewrote production state.

Production transitioned `starting → outcome_uncertain → running → succeeded`, reconciled the
original client ID through its original endpoint/history, and downloaded/reused the output.
The reservation remained `reserved` during uncertainty/history reads, then became `consumed`.
`fault-known-response.json`, `provider-reads-during-uncertainty.json`,
`provider-terminal-history.json` and the public event stream retain those facts.
This is actual provider-response loss after known completed execution, not native RPC loss,
loss during an unknown still-running execution or an application-restart experiment.

## Budget, resources and latency

- **Two actual cloud roots, two native submissions, two `/prompt` POSTs, two actual executions,
  zero children, zero further POSTs for selection/reload/reuse.** Both roots completed within 180 seconds.
- Each owner Queue created a new human generation task. The correction was not charged to the
  initial task. This run does not demonstrate exhausting a shared three-attempt task.
- Owner Queue acceptance to native work completion: **17.666806 s** initial and **7.185695 s**
  correction. POST to product job success: **5.372196 s** and **1.797791 s** respectively.
- Immediate host MemAvailable: pre-start **36721819648**, post-start **35767062528**, before
  initial POST **34046513152**, before correction POST **30763909120** bytes. All exceed the
  unchanged **12884901888-byte** floor. No wait or resampling for admission success.
- 711 resource samples. Minimum MemAvailable **29554343936** bytes, maximum owned Comfy RSS
  **2216775680** bytes. Five-GiB stopguard was not crossed.
- Whole-device GPU busy reached **100%**. Whole-device VRAM-used maximum **37459898368** bytes
  includes protected pre-existing residents and is **not** a per-process allocation peak.
- Actual runtime log: CUDA AMD Radeon 8060S, float16 model weights and CLIP, bfloat16 VAE.
  Comfy history execution intervals were **4.632 s** and **1.346 s**. `latencies.json` records
  native-root and full product completion intervals separately.
- Owned child PID `4126660`, start ticks `47273461`. Conservative pre-start-to-external-cleanup
  interval **448.55 s**, below the 1800-second allocation.

## Defects and fixture deviations

1. The first paired browser setup did not activate the project in the UI and timed out before
   any Queue. Retained as `first-setup-deviation.json`. The fixture then used the actual owner
   project activation UI before generation. No authority or job identity was injected.
2. After correction selection, the displayed image URI/hash is the correction, but the job
   selector still labels the initial image-generation job. Recorded for the coordinator.
   No unowned UI fix or relabelling was applied.
3. Uvicorn re-raised SIGTERM after lifespan shutdown, skipping the harness's outer `finally`.
   Consequently `host-report.json` remains the genuine startup report, not a fabricated final
   report. `external-cleanup.json` records exact PID/start/group/port/run-path checks and SIGTERM
   to only the owned Comfy group. `post-cleanup.json` subsequently confirms exited processes
   and empty claims. The fixture signal wrapper is now corrected and covered offline, but has
   not been claimed as a second native run.
4. Qwen context and TTL match historical M09. Its last-use timestamp differs from M09 but
   predates this run. No fresh pre-run timestamp comparison is claimed. The saved LM profile
   hash matches the historical baseline. No model-service lifecycle calls were made.

## Verification and retention

Eight focused tests pass: two offline fixture/signal tests and six retained actual-evidence
checks. Ruff format/check, mypy on four proof files and Node syntax checking pass. Existing
Starlette/httpx deprecation warning remains unrelated. No production/shared/UI/dependency
files were changed and no additional model, download, conversion, VLM, mesh or native editor
work occurred.

```bash
PYTHONPATH=.:tests uv run pytest -q tests/native/providers/m07_fp16_preflight_test.py tests/acceptance/m07_fp16_execution_test.py
uv run python tests/native/providers/m07_fp16_export.py
```

The exporter only reads retained own run receipts. It does not start a service or send inference.
`manifest.json` hashes exported evidence, including screenshots and real PNGs. Credentials,
pairing/CSRF data, browser profile and runtime database are excluded from Git. Private receipts
remain under `/home/quintin/.cache/proscenium-development/image-generation-light/runs/m07-fp16-execution-20261114/`.
Original F32/Klein failures remain separate historical evidence, not rewritten as this success.
