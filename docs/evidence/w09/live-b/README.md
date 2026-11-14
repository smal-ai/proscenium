# E04 hardware proof: actual Hunyuan mesh, Blender import and publication

**E04 passed on 14 November 2026. M10 remains incomplete pending UI/revision acceptance.**

Exactly one authorised `e04-live-b` attempt used the production `ProviderService`, shared
budget/resource APIs and accepted W02 exact-worker artefact bridge. It generated a real
image-conditioned GLB, imported and saved it in one owned Blender process, then checkpointed
and published a canonical asset through the actual W02 services. No procedural substitute,
image-plane mesh, unbudgeted submission, second attempt or renderer was used.

## Exact run and source identities

| Record | Identity |
| --- | --- |
| Accepted base | `97202858b7d032c6d9531b1c747b2aa87ef773d7`. |
| Named harness commit | `6d731787a19298d2e4bc5347536e248dd6223c35`. |
| Original background shell | `sh_1da51f070001v8TozQyDeQqUpW`, completed. |
| Retained run | `/home/quintin/.cache/proscenium-development/image-to-3d/runs/e04-live-b`. |
| Project | `project-81e5e7ee-8c36-4dee-b8a1-17c5d9de6610`. |
| Repository | `repository-9c837e96-44ac-43b1-816b-beeed754c78e`. |
| Job / attempt | `e04-mesh-job` / `attempt-a77a40c1-2a35-4f1c-96a0-c5a845c92d0e`. |
| Task / canonical target | `e04-generation-task`, one frozen allowed attempt and one consumed reservation. |
| Provider prompt | `ed610c36-0fbc-45f6-8367-d3a1b56fa329`. |
| Provider client | `b833dab6-65e2-41ab-ae5d-932b178c003f`. |
| Actual API graph SHA-256 | `af61d3da83c7fd6aaf7dfc9294d217b0dd70347888a0cdc4b9cebc638bc4d875`. |
| Source checkpoint | `94a17bb13ab6669eb4096473038be8cc8349975a`. |
| Source closure digest | `e721bc147f2f80b1a3b4e7accdc898dce2e386a51f796c3e8748b9fee2e3c0a5`. |
| Publication | `e04-publish-asset`, item `e04-generated-asset`, version `e04-asset-v1`. |

The model is the original pinned Tencent Hunyuan3D 2.0 image-only checkpoint, **4,928,151,562
bytes**, SHA-256 `360bc281fc956d4acac0c3d36d5ec0ebf8cdddbf4b8892e894d12419388d479b`.
It was rehashed before native startup. The complete official licence remains source metadata
in the published closure. No licence clearance, purchase, gated account or remote publication
is claimed.

The reference was the explicitly synthetic 512 × 512 red-mug image created inside the
disposable project, SHA-256 `038ef5c13f1efd904088be7d0753611011b8a30baac621ff914de87af6682c73`.
Actual RGB PNG bytes passed through the normal upload and image-conditioning chain.
Text was provenance only. The test's trusted admission context is synthetic and is not
reported as a Copilot SDK turn.

The graph remained unchanged: seed `1`, batch `1`, 20 Euler steps, CFG `8`, normal
scheduler, latent resolution `3072`, octree `256`, decoder chunks `8000`, surface net
threshold `0.6`. No parameter correction or additional model run occurred.

## Actual mesh and authoring result

- Native GLB: `output/mesh/proscenium-e04_00001_.glb`, **19,860,948 bytes**.
- Original-byte SHA-256: `166b1a76356067cb71e1f061f0b2217fcae91857576e2f33b0038704ed6c5a06`.
- Registered as `source_blob`, `model/gltf-binary`, then retained at
  `project/assets/e04/original.glb`. The immutable artefact, original native output and
  retained source have matching hashes.
- Actual Blender import: **350,259 vertices, 1,304,618 triangles**, one mesh object
  `E04_Mesh_001`, persistent object/instance IDs `e04-object-001` / `e04-instance-001`.
- Finite world bounds: `[-0.966773, -0.675229, -0.747808]` to
  `[0.984681, 0.674524, 0.762087]`. Object scale `[1, 1, 1]`, origin `[0, 0, 0]`.
  Scene setting is one metre per Blender unit. Real-world mug size is **not calibrated**.
- One material, **no images and no UV layers**. This is an untextured shape. No manifold,
  clean-topology, retopology, rigging, back-side accuracy or visual-fidelity acceptance.
- Canonical editable source: `project/assets/e04/asset.blend`, SHA-256
  `36e7b864fa03944f4b260478e79a385660d37eaba7a18744ea57aa79bf9558a3`.
- W02's real checkpoint/publication calls retained the original invocation/current owner
  profile and actual project repository identity. The source closure includes `.blend`,
  original GLB, conditioning PNG, graph, generation provenance, inspection and model licence.
  Publication requested no proxy/still render and made no owner selection.

## Runtime and measured resources

Actual runtime: ComfyUI **0.37.4**, installed revision
`8ff6dc384ba5c410266b40e137799e049459d4f2`, PyTorch **2.13.0+rocm10.0.0**, AMD Radeon
8060S/gfx1151. Blender **4.5.14 LTS**, build `62c1db4208e8`, exit code `0`.
The Comfy log separately reports ROCm tuple `(7, 15)`. Do not replace either observed
version identity with an inferred equivalence.

| Measurement | Actual observation |
| --- | --- |
| Host RAM before baseline / server start / inference | `15768473600` / `15939780608` / `15172407296` bytes. All above the unchanged twelve-GiB fit floor. |
| Lowest sampled host available RAM | **6,326,824,960 bytes**, approximately **5.89 GiB**. Above the five-GiB stop guard and four-GiB protected headroom. |
| Highest sampled owned Comfy RSS | **10,304,139,264 bytes**, approximately **9.60 GiB**. Includes process mappings, not an exact allocator peak. |
| Highest device-wide sampled VRAM-used counter | **41,021,833,216 bytes**. Includes independent resident services, not per-process model allocation. |
| Sampling | 171 observations, requested 0.5-second interval, maximum observed gap **1.055573407 s**. Sampled maxima can miss intervening peaks. |
| Provider dispatch through registered result | **72.981861025 s**, including upload, polling and artefact registration. Not pure GPU kernel time. |
| Comfy-reported prompt execution | **70.78 s**. |
| Blender process through import/save | **2.121109818 s**. Blender's importer separately logged **0.56 s**. |
| Retained run disk usage | **341,028,864 bytes** after completion. This excludes the already-downloaded model. |

Blender RSS was not sampled separately. No exact GPU allocator peak or general model-fit
guarantee is claimed. Four-thread settings, default Comfy memory guardrails, isolated paths,
ten-GiB disk reserve plus four-GiB run allowance and the process time bounds were retained.
The log records dynamic VRAM and its 4096-MB integrated Linux headroom enabled. It also
records missing VAE **encoder** keys. This graph uses VAE decoding only and produced the
verified output. The warning has not been hidden or relabelled as a clean log.

## Settlement and isolation

The job finished `succeeded` with complete immutable artefacts. Its single reservation is
`consumed`. There are **no remaining heavy-GPU claims or owned processes**. The owned Comfy
group was terminated with SIGTERM only after its provider result was terminal. Its PID
3375346/start ticks `45688526` and Blender PID 3382105/start ticks `45696810` were subsequently
absent. Port 18588 was verified free. The independent 8188 queue was still empty and the
installed Comfy tracked tree remained clean. No existing LM, Comfy or Blender 9876 service
was stopped, restarted, cleared, unloaded or reconfigured.

`e04-live-a/report.json` still matches its original SHA-256
`1d195a12e3c032e20252493c31ac30be80e5fab2347fb015d6aad169c0edaab8`.

## Evidence and read-only reproduction

- `native-report.json`: exact durable report, including actual graph, job, attempts,
  artefacts, Blender report, checkpoint, source closure and published version.
- `resource-samples.ndjson`: exact retained sampler output.
- `outcome.json`: condensed identities, timings, sampled measurements and limits.
- `retained-files.json`: exact sizes and SHA-256 for raw logs/reports and binary sources.
  Logs, model weights, media, runtime databases and local Git/LFS stores remain on disk,
  outside the application repository. The original source project contains its own actual
  Git/LFS checkpoint as required.

This command verifies existing output without launching anything:

```bash
PROSCENIUM_W09_LIVE_RECEIPT=docs/evidence/w09/live-b/native-report.json \
  .venv/bin/python -m pytest -q tests/native/image_to_3d/test_live_receipt.py
```

The read-only native receipt test passed. The affected offline suite passed **112 tests**,
and the historical negative A receipt still passes. No inference or Blender process was
repeated for verification. M10 still needs the actual UI/static-inspection and normal
revision/reuse journey. Any new generation needs a new explicitly bounded allocation.
