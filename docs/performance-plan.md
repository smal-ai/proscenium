# Full-stack performance plan

## Phase 1: Baseline

Measured on `2026-11-25` (SAST), before optimisation. Raw and gzip sizes are separate measurements, not necessarily the bytes served.

### Frontend build

| Metric | Baseline |
| --- | --- |
| Distribution | 56 files, 2,331.8 KiB raw / 1,195.4 KiB gzip |
| `assets/index-*.js` | 1,506.9 KiB raw / 427.9 KiB gzip, served uncompressed |
| CSS | 74.9 KiB raw / 21.2 KiB gzip |
| Fonts | 51 files, 744.2 KiB across 7 Unicode scripts |
| Rollup rendered JS (3,742.7 KiB) | `three` 2,065.2 KiB (55.2%), `@react-three/fiber` 303.1 KiB (8.1%), `three-stdlib` 165.0 KiB (4.4%), `@react-three/drei` / `its-fine` / `react-use-measure` / `suspend-react` 13.4 KiB. Together: 68.0% of eagerly bundled JS. |

### Browser

Playwright Chromium cold cache, 1920×1080, 25 items, 40 artefacts and 1 storyboard.

| Metric | Baseline |
| --- | --- |
| Milestones | TTFB 1.2 ms, FCP 68.0 ms, LCP 80.0 ms, DCL 36.1 ms, Load 36.1 ms |
| Stability | CLS 0.0754, 0 long tasks |
| Network | 94 HTTP requests, 1,806.5 KiB transferred |
| Repeated reads | 28× `/items/:id/versions`, 24× `/artifacts/:id`, 18× `/artifacts/:id/content`, 3× `/shot-plan`, 3× `/selections`, 2× `/items` |
| Disk and reload | 12 leaked `.proscenium/snapshots` directories after 1 cold open + 1 reload. Warm reload revalidates 6 `/assets/*` files. |

### Backend

`tmpfs` fixture: 40 items, 80 versions, 120 jobs, 30 reviews, 150 findings and 109 artefacts, including one 10 MiB binary.

| Operation | Time | SQLite opens / SQL | Payload or observation |
| --- | ---: | ---: | --- |
| `GET /snapshot` | 80.80 ms | 13 / 320 | 143.6 KiB raw / 5.4 KiB gzip (`enc=none`). `validate()` 55.00 ms (70.1%). |
| `GET /artifacts?limit=100` | 47.75 ms | 5 / 21 | 56.7 KiB raw / 6.0 KiB gzip. `validate()` 43.41 ms (89.2%). |
| `GET /jobs?limit=100` | 42.74 ms | 5 / 16 | 104.9 KiB raw / 3.6 KiB gzip. |
| `GET /reviews?limit=100` | 21.35 ms | 5 / 168 | 8.5 KiB raw / 0.6 KiB gzip. `validate()` 5.24 ms (24.2%). |
| `GET /items?limit=100` | 5.47 ms | 5 / 95 | 8.8 KiB raw / 0.6 KiB gzip. |
| `GET /items/item-000/versions` | 3.48 ms | 5 / 21 | 1.9 KiB raw / 0.6 KiB gzip. |
| `GET /versions/ver-sb-1/shot-plan` | 22.35 ms | 15 / 138 | 2 Git subprocesses, 1 permanent `0o500` snapshot directory leaked per GET. |
| `/artifacts/art-big/content` (10 MiB) | 14.85 ms GET / 14.94 ms HEAD / 13.74 ms 1 MiB Range | 10 / 29 | Double read and double SHA-256. `If-None-Match` returns 200 and the full 10 MiB. |
| `CatalogueBrowser` mount (39× `/items/{id}/versions`) | 129.10 ms | 195 / 819 | One versions request per item. |
| Idle `FeatureLifecycle` | — | 97.5 opens/sec | 0 active work items, jobs or tabs. An idle WebSocket adds 10 blocking opens/sec on the asyncio event loop. |

## Phase 2: Prioritised optimisation plan

Keep contract 1.0, owner and project scoping, source integrity and durable state transitions intact. File references are to the baseline checkout. Re-run like-for-like fixtures after each wave and separate measured gains from targets.

### Quick wins (P1–P9)

| Priority | Layer | Bottleneck | Evidence / File Reference | Proposed Fix | Risk |
| --- | --- | --- | --- | --- | --- |
| P1 | API / Infra | Compressible payloads served raw. Warm reload revalidates fingerprinted assets. | `app.py:78,113-131` | Add selective gzip middleware, immutable `/assets/*` and `no-cache` for the index. Offload large compression and exclude range-capable media, including GLB. | Preserve encoding negotiation, validators, ranges and already-compressed media. |
| P2 | API / DB | `GET /shot-plan` and `/sequence-map` materialise snapshots and repeatedly scan selections. | `catalogue/service.py`, `sources/resolver.py` | Verify the authored closure in memory using the same checks as `Resolver.resolve()` and preload `selections` once per transaction. | Preserve closure error details, tamper detection and selection conflict handling. |
| P3 | API | `/artifacts/{id}/content` reads and hashes twice and ignores `If-None-Match`. | `catalogue/router.py`, `catalogue/artifacts.py` | Use `ready_bytes()` for one artefact fetch, file read and SHA-256 check. Honour `If-None-Match` with 304 only after verification. | Preserve HEAD, Range and integrity checks. |
| P4 | API / DB | `Auth._owner()` takes a global `RLock` and pays a `synchronous=FULL` fsync on every GET. | `auth/__init__.py:127-145` | Read sessions via `transaction(write=False)` and throttle the 12-hour sliding-expiry write to at most once per 60 s (`expires - now < 43200 - 60`). | Preserve expiry, revocation and caller separation under concurrent requests. |
| P5 | DB / API | N+1 full-table scans in catalogue, reviews, snapshot and sequence validation. | `catalogue/service.py:50-104`, `reviews/routes.py:28,62`, `reviews/store.py:165-255`, `projects/service.py:619-634`, `catalogue/validation.py:379` | Preload `selections`, `jobs`, `findings`, `review.meta`, `review.requests` and `review.diagnostics` once per read transaction, remove duplicate `tx.all("reviews")` and cache `source_job` inside `sequence_map`. | Keep project scope, validation and ordering unchanged. |
| P6 | FE | Static 3D proxies render continuously and propagate camera changes. | `features/review/ProxyView.tsx:133-142,554,566` | Switch `<Canvas>` from `frameloop="always"` to `"demand"`, explicitly `invalidate()` on interaction, load and resize, and throttle camera propagation to `ReviewWorkspace` and `localStorage`. | Verify no stale frames or lost final camera state. |
| P7 | FE | `SequencePlayer` allocates a canvas and scans frame mappings during playback. | `features/review/SequencePlayer.tsx:37-45` | Reuse one offscreen `<canvas>` inside `requestVideoFrameCallback` and replace `mapping.frames.some` with a `useMemo` `Set<number>`. | Preserve capture and mapping updates when the source changes. |
| P8 | FE | 51 font assets across seven Unicode scripts. | `app/StudioApp.tsx:23-26` | Import `@fontsource/inter/latin-{400,500,600}.css` and `@fontsource/jetbrains-mono/latin-400.css`. Expect eight assets, retaining `U+0000-00FF`, `U+2000-206F`, `×`, `·`, `—`, `…` and `€`. | Verify required glyphs and fallback text. |
| P9 | API | Duplicate sequential ComfyUI `/object_info` probes. | `copilot/capabilities.py:76-163` | Deduplicate by `(provider_id, endpoint, path)` and probe distinct targets concurrently. | Preserve capability order, bounded concurrency and failure handling. |

### Structural improvements (P10–P14)

| Priority | Layer | Bottleneck | Evidence / File Reference | Proposed Fix | Risk |
| --- | --- | --- | --- | --- | --- |
| P10 | FE | Three.js, React Three Fiber and Drei account for 68.0% of initial JS. | `media.ts:1`, `spatial.ts:1`, `renderFraming.ts:1`, `ReviewWorkspace.tsx:12`, `ProxyView.tsx` | Split non-3D helpers out of modules importing `three` and use `React.lazy()` to load `ProxyView.tsx` only on entering `3d_inspection`. | Verify the initial chunk no longer imports 3D dependencies and first entry still works. |
| P11 | API | `jsonschema` `validate()` accounts for 70–89% of read latency on affected routes. | `contracts/__init__.py:16-20`, `catalogue/artifacts.py:74,84`, `projects/service.py:636`, `reviews/store.py:238`, `reviews/routes.py:77` | Use `@lru_cache` for compiled `Draft202012Validator` instances. Paginate before building and validating `RetainedArtifactSummary`. Remove double validation and redundant re-validation of immutable DB records validated on write. | Keep trust-boundary validation and contract 1.0 intact. |
| P12 | DB / API | SQLite connection and migration churn, 97.5–107.5 idle opens/sec and blocking WebSocket polling. | `db/store.py:45-98`, `db/registry.py:78-118`, `api/lifecycle.py:12-78`, `copilot/router.py:178-223` | Cache migrated `(path, dev, ino)`, set `journal_mode=WAL` once on migration, combine registry reads and cache the active project. Offload WebSocket SQLite reads with `asyncio.to_thread`, wake on commits via `asyncio.Event` and back off idle `FeatureLifecycle` pollers. | Verify store replacement, project switches, reconnect, event replay and recovery. |
| P13 | FE | Initial 94-request waterfall. | `StoryboardBrowser.tsx:104-155`, `ReviewWorkspace.tsx:389-426`, `CatalogueBrowser.tsx:98-107`, `useProjectData.ts:292-301` | Stop the `StoryboardBrowser` double shot-plan fetch caused by the `records` dependency. Reuse `items` and `retainedArtifacts` across views, batch `setArtifacts`, parallelise independent `ReviewWorkspace` fetches, coalesce `refreshSnapshot()`, drop redundant `listJobs` when snapshot succeeds, retain `listItems` in `hydrate()` and stabilise catalogue version fetches. | Preserve hydration, publication invalidation and selection changes across views. |
| P14 | FE | Keystroke and WebSocket-event re-render storms. | `ChatPanel.tsx:114-125`, `Conversation.tsx:54-73,114`, `useProjectData.ts:368-370`, `StudioApp.tsx:238-286`, `ReviewWorkspace.tsx:795`, `CatalogueBrowser.tsx:50` | Pre-index `retainedArtifacts` and `events` with `useMemo`, memoise `Conversation`, append ordered events incrementally and add dependency arrays to `window` keydown listeners. | Preserve event order, reconnect backfill and current keyboard state. |

### Explicitly deferred

| Item | Decision |
| --- | --- |
| SQLite durability | Keep `synchronous=FULL`. Do not trade crash durability for speed. |
| SQLite storage model | Keep the EAV schema. Optimise reads before considering a storage rewrite. |
| Batch HTTP routes | Do not add batch routes absent from the OpenAPI contract. Batch internal queries instead. |
| Hydration item read | Keep `listItems` in `hydrate()`. The [browser acceptance test](../tests/browser/uat-batch.spec.ts#L900-L905) verifies item reads across hydration and publication invalidation. |
| List virtualisation | Defer until large-fixture profiling identifies rendering, rather than request fan-out, as the limiting factor. |

## Phase 3: Verified Results

| Wave | Priorities | Metric and fixture | Baseline | Verified result | Evidence |
| --- | --- | --- | --- | --- | --- |
| 1 | P1 | `GET /snapshot` wire size | 143.6 KiB | 4.8 KiB (−96.7%, `enc=gzip`) | `tmpfs` fixture |
| 1 | P1 | `GET /jobs?limit=100` wire size | 104.9 KiB | 3.6 KiB (−96.6%, `enc=gzip`) | `tmpfs` fixture |
| 1 | P1 | `GET /assets/index-*.js` wire size and cache header | 1,506.9 KiB, `cc=none` | 426.1 KiB (−71.7%, `enc=gzip`), `cc=public, max-age=31536000, immutable` | `tmpfs` fixture |
| 1 | P1 | `GET /` (`index.html`) cache header | `cc=none` | `cc=no-cache` | `tmpfs` fixture |
| 1 | P1, P2 | `GET /versions/ver-sb-1/shot-plan` | 22.35 ms, 138 SQL, 4 leaked snapshot directories / 4 GETs, 9.4 KiB | 18.70 ms, 91 SQL, 0 leaked directories, 0.9 KiB (`enc=gzip`) | `tmpfs` fixture |
| 1 | P3 | 10 MiB `/artifacts/art-big/content`, GET / HEAD / 1 MiB Range, plus `If-None-Match` | 14.85 / 14.94 / 13.74 ms, 10 opens, 29 SQL. `If-None-Match`: 200 (10,240 KiB). | 9.47 / 9.10 / 7.55 ms, 6 opens, 19 SQL. `If-None-Match`: 304 (0 KiB). | `tmpfs` fixture |
| 1 | P1 | `GET /content` for a 1 MiB PNG | `enc=none` | `enc=none` (compression skipped) | `tmpfs` fixture |
| 2 | P4, P5, P11, P12 | `GET /snapshot` latency, SQLite opens and SQL | 80.80 ms, 13 opens / 320 SQL | 11.00 ms (−86.4%, 7.3× faster), 10 opens / 60 SQL (−81.3% SQL) | Phase 1 `tmpfs` fixture, full exact-bytes contract 1.0 proofs |
| 2 | P4, P5, P11, P12 | `GET /jobs?limit=100` latency, SQLite opens and SQL | 42.74 ms, 5 opens / 16 SQL | 3.64 ms (−91.5%, 11.7× faster), 3 opens / 9 SQL | Phase 1 `tmpfs` fixture, full exact-bytes contract 1.0 proofs |
| 2 | P4, P5, P11, P12 | `GET /artifacts?limit=100` latency, SQLite opens and SQL | 47.75 ms, 5 opens / 21 SQL | 9.97 ms (−79.1%, 4.8× faster), 3 opens / 13 SQL | Phase 1 `tmpfs` fixture, full exact-bytes contract 1.0 proofs |
| 2 | P4, P5, P11, P12 | `GET /reviews?limit=100` latency, SQLite opens and SQL | 21.35 ms, 5 opens / 168 SQL | 5.30 ms (−75.2%, 4.0× faster), 3 opens / 15 SQL (−91.1% SQL) | Phase 1 `tmpfs` fixture, full exact-bytes contract 1.0 proofs |
| 2 | P4, P5, P11, P12 | `GET /items?limit=100` SQLite opens and SQL | 5 opens / 95 SQL | 3 opens / 11 SQL (−88.4% SQL) | Phase 1 `tmpfs` fixture, full exact-bytes contract 1.0 proofs |
| 2 | P4, P5, P11, P12 | `GET /items/item-000/versions` latency, SQLite opens and SQL | 3.48 ms, 5 opens / 21 SQL | 2.65 ms, 3 opens / 12 SQL (−42.9% SQL) | Phase 1 `tmpfs` fixture, full exact-bytes contract 1.0 proofs |
| 2 | P4, P5, P11, P12 | Idle SQLite polling (`manage_features=True` and one idle WebSocket) | 107.5 opens/sec (97.5 lifecycle + 10.0 blocking WebSocket) | 9.5 opens/sec (−91.2%): 1.5 lifecycle + 1.0 non-blocking WebSocket + 7.0 from the 1 s tick in `copilot/service.py` | Phase 1 `tmpfs` fixture |
| 3 | P6, P7, P8, P10 | Entry JS (`assets/index-*.js`) raw and wire sizes | 1,506.9 KiB raw, served uncompressed (68.0% Three.js, React Three Fiber and Drei) | 486.5 KiB raw (−67.7%), 150.8 KiB gzip (−90.0% wire). Zero bytes of `three`, `@react-three/*` or `three-stdlib` in the entry chunk. The lazy `ProxyView-*.js` chunk holds 1,020.3 KiB raw / 277.6 KiB gzip. | Vite production build (`apps/frontend/dist/`) |
| 3 | P6, P7, P8, P10 | Entry CSS (`assets/index-*.css`) raw and wire sizes | 74.9 KiB raw | 57.0 KiB raw (−23.9%), 12.0 KiB gzip (−84.0% wire) | Vite production build (`apps/frontend/dist/`) |
| 3 | P6, P7, P8, P10 | Font assets and total distribution files | 51 font files (744.2 KiB) across seven Unicode scripts, 56 files in `dist/` | 8 Latin font files (209.3 KiB, −71.9%), 15 files in `dist/` | Vite production build (`apps/frontend/dist/`) |
| 4 (cumulative) | P1–P14 | Cold-cache transferred and decoded bytes | 1,806.5 KiB transferred / 1,779.3 KiB decoded | 298.5 KiB transferred (−83.5%, 6.1× smaller) / 734.0 KiB decoded (−58.7%) | Playwright Chromium 1920×1080, Phase 1 fixture: 25 items, 40 artefacts, 1 storyboard |
| 4 (cumulative) | P1–P14 | Cold-cache HTTP waterfall | 94 requests, including 24× `/artifacts/:id`, 2× `/items` and 1× `/jobs` | 68 requests (−26), including 0× `/artifacts/:id`, 1× `/items` and 0× `/jobs` | Playwright Chromium 1920×1080, Phase 1 fixture: 25 items, 40 artefacts, 1 storyboard |
| 4 (cumulative) | P1–P14 | Cold-cache Web Vitals and stability | LCP 80.0 ms, CLS 0.0754, 0 long tasks | LCP 64.0 ms (−20.0%), CLS 0.0234 (−69.0%), 0 long tasks | Playwright Chromium 1920×1080, Phase 1 fixture: 25 items, 40 artefacts, 1 storyboard |
| 4 (cumulative) | P1–P14 | Snapshot directory leaks and warm revisit and reload `/assets/*` server hits | 12 leaked `.proscenium/snapshots` directories, 6 assets revalidated | 0 leaked directories, 0 asset requests to the server (`immutable` cache hits) | Playwright Chromium 1920×1080, Phase 1 fixture: 25 items, 40 artefacts, 1 storyboard |
