# Workload-scoped performance measurements

## M14 viewport allocation

Base `0c77d899ce008a6502c2a4a4265d42ab794e3542`. This task measures the maintained `ProxyView` component in a controlled compiled fixture. It is not a new backend/native journey, whole-studio shell benchmark, browser matrix or M14/release acceptance.

Preflight builds the maintained studio and a fixture importing the unmodified `ProxyView` and its normal `useGLTF`, Canvas, OrbitControls, CameraObserver and CaptureBinding implementation. The fixture has a 1600 × 900 canvas inside a 1920 × 1080/DPR1 viewport. Only the exact hash-verified small Geometry Nodes GLB/mapping and accepted old M10-v1 GLB/mapping are copied into a fresh owned home-cache fixture. M10 SQLite, source and session state are never read. No SDK, authoring, export, inference, native encoder, service operation or geometry reduction is part of this allocation.

One disposable Chromium profile uses the accepted SwiftShader flags. Small then heavy load once, without an HTTP cache, in the same browser. Shader/JavaScript state and the small asset can remain warm for the second case. A normal maintained framebuffer capture verifies shaded geometry and changed camera/pixels after scripted pointer orbit before sampling.

The observation hook forwards actual browser rAF callbacks and GL draw calls unchanged. It retains timestamps only for rAF callbacks that issued a GL draw, with JavaScript callback duration and draw count. It does **not** call `gl.finish`, use a GPU fence, time GPU completion or measure compositor presentation. Prospective targets will be derived from the observations and labelled for this software-rendered fixture, not declared universal SLAs.

Guards: four GiB available host RAM, two GiB **summed attributed Chromium-process RSS** (not PSS, so shared pages can be counted more than once), one browser launch/profile, two cases and 240 seconds total after preflight. The safety shutdown begins at 235 seconds. Exact PID/start/parent/process-group identities attribute the root and descendants, including previously observed orphans. Independent processes are neither signalled nor included. A guard stop retains actual partial samples rather than padding to 120/60 callbacks. No resource wait or guard override is allowed.

Preflight commands:

```bash
uv sync --frozen
npm ci --cache "$HOME/.cache/proscenium-development/npm" --ignore-scripts
npm run build
node --test tests/acceptance/m14_performance_helpers.test.mjs
node --check tests/native/studio/m14_performance.mjs
node_modules/.bin/tsc --noEmit --jsx react-jsx --module ESNext \
  --moduleResolution bundler --target ES2022 --lib ES2022,DOM \
  --esModuleInterop --skipLibCheck tests/acceptance/m14_performance_fixture.tsx
node tests/native/studio/m14_performance.mjs prepare \
  "$HOME/.cache/proscenium-development/milestones/m14-performance-20261114-a"
```

The compiled studio and fixture builds, pure helper tests and fixture typecheck passed before measurement. Existing Lucide directive and large-bundle warnings remain visible. Exact source, lockfile, copied proxy/mapping and built asset fingerprints are retained by the driver. The measured outcome and historical renderer/provider rows are recorded after execution, not assumed by this preflight document.

## Actual outcome: partial harness failure, NFR04.2 remains open

Tested driver commit **`fe55f8d`**, measured production component revision **`0c77d899ce008a6502c2a4a4265d42ab794e3542`**. The single allocated browser/server run lasted **1.954345 s**. It loaded the real small proxy and captured actual visible geometry, then stopped because the harness required **more than sixteen quantised colours**. The flat-shaded block actually contains **ten** quantised colours and **15,554 sampled non-background pixels**. The retained PNG plainly shows its shaded faces. This is a bad palette heuristic, **not an observed product performance failure**.

The predicate is corrected and pure-tested against the exact recorded pixel statistics and a blank-frame negative. It has **not** been run in another browser. The original reservation, source hashes and unsuccessful result remain intact. No replacement profile, browser, server or measurement was started. The heavy GLB and mapping were copied and hash-checked only.

| Actual observation | Result |
| --- | --- |
| Small GLB | **1,788 bytes, 12 triangles, 24 export-position records**, SHA-256 `2b5f01e288df8ad19c313507f285351347b2c951ebcb71000ea89930e484825c` |
| Small mapping | **614 bytes**, SHA-256 `9d451294e1ba757da78000ec97b05128c5d10f565062932eba38a420f7098a3e` |
| Browser resource interval | **28.0 ms**, `encodedBodySize` and `decodedBodySize` **1,788**, browser-reported `transferSize` **2,088** |
| Loopback server request-to-finish | **7.842774 ms**, **1,788 body bytes** |
| Load-button start → maintained CaptureBinding ready | **417.7 ms** |
| Actual capture | **1600 × 900**, correct default camera and shaded blue procedural block |
| Orbit verification | **Not reached** |
| Steady render-loop/rAF samples | **0**. No p50/p95/max cadence exists. |
| Heavy browser load/cadence | **Not run**. Prepared GLB **90,077,656 bytes**, **1,304,618 triangles**, **3,100,876 export-position records**. |
| Browser/process samples | **9**, approximately 200 ms interval |
| Highest sampled attributed summed RSS | **1,604,452,352 bytes**, approximately **1.494 GiB**, below the two-GiB guard |
| Lowest sampled host MemAvailable | **32,346,054,656 bytes**, approximately **30.125 GiB**, above four-GiB guard |
| Host load average, first → last | **11.14 / 12.57 / 12.53 → 10.97 / 12.51 / 12.51** |

The 417.7 ms interval includes mapping retrieval, component/WebGL setup, GLTF decode/scene clone and readiness effects. It is not pure parse time or first compositor presentation. Server finish is the HTTP response stream's completion, not a TCP acknowledgement. Browser ResourceTiming transfer size is not a packet capture. Loopback timings include scheduling and host contention. No bandwidth, orbit, steady-rate or GPU-completion result is inferred from this short load.

Runtime: **Chromium 159.0.8154.6**, **1920 × 1080 / DPR1**, WebGL2, `ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero) (0x0000C0DE)), SwiftShader driver)`. Host **Ubuntu 26.04.1 LTS**, Linux **7.0.0-43-generic**, x64, **AMD RYZEN AI MAX+ 395 w/ Radeon 8060S**, **32 logical CPUs**, Node **v24.18.0**. This was software WebGL, not a physical-GPU workload.

The small proxy came only from the accepted Geometry Nodes-b immutable artefact. The heavy proxy/mapping came only from the exact old M10-v1 paths/hash records in `docs/evidence/m10/retained-files.json`. No M10 database, source or session was opened. **All four original files rehash identically after the run.** Heavy SHA-256 remains `7631a575fa9dfb3478fbbb5fa26dcff0f6ff4741956856933f23e6c8be0c219e`.

### Prospective regression targets, not a green result

- **Small loader candidate: 727 ms**, calculated as `ceil(417.7 × 1.5 + 100)`. This gives a 50% multiplier plus 100 ms scheduling margin on this one observed cold-profile load, for the exact component/host/browser/software-rendered workload above. It is provisional, not validated by repeated runs or orbit/cadence.
- **Small/heavy cadence and heavy-load targets: unset.** No observations exist from which to derive them. Filling these cells would manufacture evidence.
- The four-GiB/two-GiB limits remain **safety guards**, not workload-derived regression thresholds. Sampled RSS can miss peaks and sums shared pages repeatedly. The current short run does not qualify a heavy workload memory budget.
- A replacement browser allocation is required to complete the requested 120/60 callback observations and derive defensible cadence targets. No additional execution is assumed authorised.

## Historical renderer/provider measurements, not rerun

These rows retain their original code/runtime identities. They are **not measurements on `0c77d89`**. [Machine-readable join and source fingerprints](../evidence/m14-performance/historical.json).

| Historical workload | Measured result | Original tested identity and limits |
| --- | --- | --- |
| [W03 native](../evidence/w03/native.json), twelve-frame fixture, Cycles CPU8, four threads, 640×360 | First published frame **1.284406 s**, first segment **3.259372 s**, frame median/p95 **0.570523 / 0.616550 s**, supervisor **7.675434 s** | Base `66f653bcf1ef036a7473c7784fd15dd94db5a6a7`, renderer `67adfba53febf780535fba1d5d3a2823380c0e20`. Internal renderer peak **27.15M**, **no OS RSS/CPU/GPU telemetry**. Original integration-gap note is preserved, not reinterpreted as current state. |
| [M08 final-run](../evidence/m08/final-run.json), model-authored motion, same CPU8/640×360 profile | First producer-ready PNG **0.885866 s**, segment **2.954548 s**, frame median/p95 **0.647846 / 0.764987 s**, supervisor **8.519851 s**, **1.408475 frames/s** | Accepted dependency parent `4607c17`, driver/evidence `2d7a32ab93875c8ea507938b045c3267c20ddb56`. Internal renderer peak **27.14M**, **OS RSS and CPU percentage not sampled**. Browser playback callback observations are not viewport-cadence replacements. |
| [E04 live-b](../evidence/w09/live-b/README.md), Hunyuan image→mesh | Provider dispatch through registered result **72.981861 s**, Comfy prompt **70.78 s** | Harness `6d731787a19298d2e4bc5347536e248dd6223c35`; Comfy0.37.4 revision `8ff6dc384ba5c410266b40e137799e049459d4f2`, torch2.13.0+rocm10.0.0, AMD8060S/gfx1151. **171 samples**, minimum host available **6,326,824,960 bytes**, max owned Comfy RSS **10,304,139,264 bytes**. Device-wide VRAM includes independent services. No exact allocator peak or Blender RSS. |
| [M09 initial](../evidence/m09/vlm-responses-redacted.json), Gemma4-E4B | **10.786324 / 4.563040 s** | Base `b1fa32c8a73d5aca8243fa82e65837a8acaba75c`. First response exhausted 512 tokens with empty content, second mixed evidence classes. Failed quality remains visible. No per-response OS RSS or allocator peak. |
| [M09 positive](../evidence/m09-positive/README.md), Gemma4-E4B visual review | **4.532092 / 5.587154 s**, prompt/completion tokens **252/162** and **417/210**, zero reasoning tokens | Base `ad47260a922612393896c21ff7186d643037c524`, driver `b8a68c043c361c53b12ee2ca2328daa944465cdf`; 65536 context, batch4096/ubatch2048, K/Vf16, flash **auto**, reasoning none, max512. Host MemAvailable before POST is not process RSS or exact allocator peak. |

None of these end-to-end provider durations is pure GPU kernel time or a measured steady tokens/s result. M07's returning inference measurements will be joined by the coordinator, not rerun here.

## Cleanup, evidence and remaining decision

Owned browser root **PID 4168896 / start ticks 47366920**, process group **4168896**. Exact attributed child identities/RSS and cleanup observations are in [measurement.json](../evidence/m14-performance/measurement.json). CDP close did not immediately retire the persistent browser, so the harness terminated only its recorded PID/start-matching processes. Final root is absent, no attributed live process remains, and the one loopback server in harness **PID 4168864 / start ticks 47366875** is closed. A later exact-identity read confirmed both original browser and harness processes absent. Source originals are byte-identical. No independent process was signalled. No SDK/native authoring/renderer/provider inference/encoder/service action occurred.

Private retained root: `/home/quintin/.cache/proscenium-development/milestones/m14-performance-20261114-a`. [Summary](../evidence/m14-performance/summary.json), [actual canvas](../evidence/m14-performance/small-before.png), and raw source/build/test/process/time records are retained. The corrected palette predicate is **not** substituted into the original tested source fingerprints.

Read-only export and tests, without a new browser/server:

```bash
.venv/bin/python tests/native/studio/m14_performance_report.py \
  "$HOME/.cache/proscenium-development/milestones/m14-performance-20261114-a"
node --test tests/acceptance/m14_performance_helpers.test.mjs
.venv/bin/pytest -q tests/acceptance/m14_performance_evidence_test.py
```

**Coordinator decision required:** authorise one replacement measurement after the proven palette-check defect, with the same retained proxies and unchanged time/RSS/headroom guards, or retain this as partial. No replacement was launched. **NFR04.2 cadence and M14/release acceptance remain open.**

Final non-browser verification: five pure Node helper tests and five retained-evidence tests passed, including the actual ten-colour image regression. Fixture TypeScript, Node syntax, Python mypy/Ruff and specification validation passed. The studio/fixture builds passed before the measurement, with existing bundle/Lucide warnings. The Starlette/httpx warning remains. No failed browser stage was rerun to obtain these checks.
