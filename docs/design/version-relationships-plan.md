# Implementation plan: published relationships, Outliner and reusable scene inputs

- **Owner decision, 28 November 2026 (SAST):** Proceed with the three-increment build. Keep `asset`, `scene`, `storyboard`, `sequence` and `unity_result`. Do not add a dedicated `character` item kind. `character`, `motion`, `wardrobe` and similar labels are optional searchable tags, not capabilities.
- **Status:** Waves 1–5 are implemented on `feat/version-relationships`. [Wave 4 evidence](version-relationships-wave4-2026-11-28.md) records composition and recovery. [Wave 5 evidence](version-relationships-wave5-e10-2026-11-28.md) records the bounded E10 pass and the three baseline failures still present in the full browser suite. This is the branch delivery record, not release or deployment approval.
- **References:** Follow the [design](version-relationships.md), its [acceptance tests](version-relationships.md#9-acceptance-tests), the [owner policy](../owner-policy.md) and [contract compatibility rules](../contracts/README.md#compatible-uat-extensions). Use ISO 8601 dates in evidence filenames (for example `2026-11-28`) and 24-hour SAST for recorded local times.
- **Path shorthand:** Backend module paths below are relative to `apps/backend/src/proscenium/` and frontend module paths to `apps/frontend/src/`. Contract, test, skill and operations paths are relative to the repository root.

## Wave 1 — Increment 1A: backend relationship index, recovery migration and owner read API

| Surface | Work |
| --- | --- |
| `docs/contracts/schema.json`, `docs/contracts/openapi.yaml`, `docs/contracts/README.md` | Define closed `RelationshipPage`, `RelationshipEdge`, `RelationshipNodeSummary`, `RelationshipEndpoint` and `RelationshipCoverage` records. Add `getVersionRelationships`: owner `GET /api/v1/projects/{project_id}/versions/{version_id}/relationships`. Accept `direction=uses\|used_by` (default `uses`), optional board-version-local `shot_id`, signed keyset `cursor` bound to project, exact root, direction, sort and `relationship_revision`, and `limit` 1–100 (default 50). Document the additive extension and run `make generate`. |
| `db/store.py`, new `catalogue/relationship_projector.py` and `catalogue/relationship_read.py` | Add a `MIGRATIONS` entry for disposable per-project relationship tables with indexed outgoing/incoming keys and typed local-occurrence identity, per-version coverage and a monotonic project relationship revision. Use the same pure projector over validated version, storyboard and sequence evidence from `Catalogue.publish` in `catalogue/service.py` and `Import.commit` in `projects/importer.py`. Persist rows, coverage and revision in the version transaction. Failed writes and idempotent replays change none of them. |
| Backfill and recovery | Index known pins and lineage first. Read exact sealed documents outside GETs and long write transactions. Backfill in bounded, restartable batches, fencing application of projected rows against concurrent publication/import. In `tools/operations/recovery.py`, make `existing_store` and `restore` verify untouched pre-migration bundles at their supported database version. Reject unknown future versions. Migrate only the restored project and registry copies and rebuild the project projection there. |
| `catalogue/router.py` and relationship reader | Apply owner authentication and active-project scope, not tool or worker authority. Read one hop via indexed `limit + 1` keyset paging in a consistent read transaction. Keep canonical edge orientation, authored order, typed occurrence identity and existing selection/status overlays. No GET-time writes, all-version scan, Git/LFS, snapshot, native or provider work. Report partial coverage independently of page exhaustion. |

| HTTP result | Condition |
| --- | --- |
| `200` | Existing root. Return known rows and `partial` coverage if evidence is incomplete. |
| `400 invalid_request` | Invalid query values or `shot_id` on a non-storyboard. |
| `400 invalid_cursor` | Malformed token or mismatch in bound scope, root, direction or sort. |
| `401` | Unauthenticated owner read. |
| `403 scope_denied` | Inactive project or wrong caller authority. |
| `404 not_found` | Unknown in-project version or a shot known not to exist on that board version. |
| `409 relationship_revision_changed` | Valid cursor for a superseded revision. Instruct the caller to restart at page one. |

**Exit:** Service and contract tests cover E1, E2, E4 and backend E5: structural reads without production, all-history reverse lookup, identical typed rows on publish/import/rebuild for existing edge types, rollback/replay/restart, interrupted backfill, partial coverage and repair, caller separation, swapped identities, cursor scope and revision drift. Restore a verified pre-migration fixture into a fresh destination, compare the rebuilt projection and prove the source bundle was untouched. On the [Studio performance fixtures](../performance-plan.md) and a synthetic project with 1,000 versions, 10,000 edges and more than 100 direct consumers of one version, record `EXPLAIN QUERY PLAN`, request counts, bytes read and cold/warm timings. Show indexed page-bounded lookups and traverse a stable revision with zero gaps or duplicate edges. Do not assign an unmeasured latency target.

## Wave 2 — Increment 1B: read-only Outliner and 3D Inspection objects

| Surface | Work |
| --- | --- |
| `features/review/Outliner.tsx` or new `features/outliner/`, wired through `main.tsx`, `app/StudioApp.tsx` and `api/studio.ts` | Replace the proxy-only tree with version relationships. Use the exact viewed root or a project-local picker. Show **Uses** and **Used by**, one-hop expansion, a cycle guard on the current path, structural/input/provenance groups and historical consumer badges. Distinguish partial coverage, failed requests and complete empty results. **Refresh relationships** rereads the current revision without triggering a rebuild. Invalidate visible graph reads on publication, reactivation or reconnect and discard stale requests on collapse or root/project change. |
| Navigation and isolation | Expansion, collapse and filters never change Review, Composer targets or references, owner selections or drafts. **Open version**, **Open shot in Shots** and **Inspect panel** use existing explicit pinned navigation and preserve drafts. Support keyboard navigation, focus and back navigation. Opening the graph loads no Three.js and fetches no proxy or media. |
| `features/review/ReviewWorkspace.tsx` | Move the static proxy selector to **3D Inspection → Objects at frame N**. Bind only to the viewport's active `mapping_artifact_id`, `proxy_artifact_id`, `sample_frame`, version and digest. Preserve Ctrl/Cmd multi-select and stable `(object_id, instance_id)` pairs. Clear incompatible selections on mapping change and never substitute a newer map when an old pinned map is unavailable. |

**Exit:** Frontend unit and Playwright tests cover E1, E2, browser E5 and E6: exact-version browsing with no proxy, repeat uses on separate paths, keyboard-only navigation, stale responses, render-tick floods while hidden/collapsed, no Composer or draft drift, and multi-map historical 3D Inspection with persistent object pairs and multi-selection. Inspect the built entry chunk and confirm it contains no Three.js.

## Wave 3 — Increment 2: per-shot storyboard intent

| Surface | Work |
| --- | --- |
| `docs/contracts/schema.json`, `docs/contracts/README.md` | Add optional `planned_assets: VersionRef[]` with `uniqueItems: true` to authored `Shot` and projected `ShotPlan.shots[]`. Preserve absent versus explicit empty on the wire. Document the extension and run `make generate`. |
| `catalogue/validation.py`, `catalogue/service.py`, `projects/importer.py` | Share checks for exact item/version match, active project and `kind == "asset"` across standalone validation, preflight, publish, read projection and import. At publication, deduplicate the union of board-wide assets, per-shot planned assets and bound scenes and require it in `source.dependencies`. Reject a missing closure pin rather than rewriting sealed bytes. Resolve eligible same-batch refs, leave board-wide pins unchanged, and do not hydrate assets for plan validation or reading. |
| `copilot/capabilities.py`, `agent/skills/storyboard/plan_tools.py` | Import one shared pure `impact`/`compare` helper. Compare both revisions and treat `planned_assets` as a set. Report shot-local potential impact for local changes and conservative all-shot impact for global pins. Preserve existing order-change behaviour and conservative untyped item-ID matches. |
| Relationship projector, `features/storyboards/StoryboardBrowser.tsx` and Outliner | Emit `planned_asset` edges and show shot intent separately from board references, scene bindings, production inclusion and owner choice. Do not infer a pin from prose or inherit selections across board versions. |

**Exit:** E3 covers legacy absence, explicit empty, local versus global pins, overlapping declarations, rejected duplicate list entries, unknown/mismatched/wrong-kind refs, missing closure pins and valid batch publication. Prove host/checkout compare and impact parity for changed, removed and reordered shots or pins, and verify owner selection isolation across board versions in Studio. For E4, prove publish, import and rebuild produce identical typed `planned_asset` edges.

## Wave 4 — Increment 3: pinned source composition

| Surface | Work |
| --- | --- |
| `docs/contracts/schema.json`, `docs/contracts/README.md` | Add optional `CheckpointRequest.include_sources: VersionRef[]` requiring `entry_path`, and optional `SourceClosure.included_sources: VersionRef[]` as the effective direct includes. Require `included_sources` to be a subset of `dependencies` and omit it when empty. Document semantics and run `make generate`. |
| `sources/checkpoint.py` | Merge included sealed manifests separately from owned destinations. An omitted `include_sources` retains composed-base inclusions, `[]` detaches them and a supplied list replaces them. Apply exclusions to inherited owned files, not active includes. Share a destination only for identical full `ClosureFile` rows including origin. Reject other collisions and file/directory prefixes. Preserve the consumer-owned entry and its applicable commit rule. Check 128 direct includes, 4,096 expanded destinations and 512 MiB hydrated bytes before intent or ref changes. Sort new unordered refs before fingerprinting/digest and exclude requests with `include_sources` from `_legacy_completed_replay` fallback. |
| `sources/resolver.py`, `sources/read_only.py`, `catalogue/service.py`, `copilot/capabilities.py` | Share inclusion validation across checkpoint, execution, read verification, authored preflight and inline/receipt-backed publication. Reject fabricated inline `included_sources` even with a valid digest: each direct ref must be exact and pinned and its manifest rows present unchanged. Use one unambiguous row per destination for composition and align verifier handling without changing legacy digest rules. Do not traverse ordinary dependency or lineage links to stage files. |
| `copilot/tools.py`, `copilot/tool_schemas.py`, `guidance.py` | Preserve `included_sources` in the `result_text` large-closure projection. Add portable model-schema projection tests and reviewed static model-safe reasons for all new failures. The model passes the sealed receipt or operation ID, not a reconstructed manifest. |
| `projects/importer.py`, `tools/operations/recovery.py`, relationship projector | Apply the same validation where import can represent composed origins. Explicitly reject a multi-commit representation the offline manifest cannot encode instead of flattening origins. Normal backup/restore must preserve all original Git/LFS and retained file origins and composed closures. Emit `includes_source` edges only for verified inclusion evidence. |

**Review the four traps in [design §2](version-relationships.md#2-review-of-the-current-implementation):** verify shared destination handling without changing historical digests, prove both version writers and backfill preserve reverse coverage, test large receipts and legacy replay explicitly, and measure peak memory and integrity-check cost for a linked animated scene. Count identical shared rows once, check declared sizes before bulk reads and verify actual sizes/hashes with bounded I/O. Do not loosen integrity checks to improve the measurement. Measure the path at the new bounds before enabling it and complete the native linked-scene measurement in Wave 5.

**Exit:** For E4, prove publication, rebuild and rebuild after normal backup/restore produce identical typed `includes_source` edges. Test explicit offline import rejection when the manifest cannot encode multi-commit composed origins. E7 and E8 cover pinned bytes after worktree drift, sealed transitive files, identical shared origins, retained files, local LFS, inline and large receipt-backed publication, collision and limit rejections, lost acknowledgements and legacy digest/replay fixtures. E9 adopts wardrobe v4 selectively in the stairwell and later board revision, proves v3-only files do not leak into v4, and proves the old landing, board and sequence keep v3. Restore into a fresh private destination without HEAD fallback and show both histories in the Outliner. Run `tests/test_tool_guidance_audit.py` and `tests/test_tool_schema_and_guidance.py`.

## Wave 5 — Skills, prompt routing, docs and bounded native Blender proof

| Surface | Work |
| --- | --- |
| `agent/skills/{storyboard,hero-assets,blender-authoring,mpfb2,mixamo,research,unity}/SKILL.md`, `agent/skills/storyboard/templates.md` | Teach board-wide versus shot-local intent, explicit adoption, portable pinned library paths, scene-owned per-instance timing and the difference between motion source, retained Action and rendered video. Preserve upload approval, source attribution and MPFB safeguards. Do not promise Unity skinning or animation export. |
| `agent/skills/blender-authoring/SKILL.md`, `agent/skills/mpfb2/SKILL.md` | Require the [Design §6 pre-edit check](version-relationships.md#authoring-is-not-materialisation): verify the actual linked library files against the selected published origins before interactive editing. Stop if they differ or cannot be verified. Exercise this refusal in E10. |
| `copilot/native.py` (`apps/backend/src/proscenium/copilot/native.py`) | Route via existing capability, catalogue, validation, checkpoint and publication tools. Keep prompt changes short. No owner GET via borrowed credentials, new always-on graph tool or broader worker approval. |
| `docs/studio.md`, `docs/specification.md`, `docs/operator.md`, `docs/contracts/README.md`, `docs/design/version-relationships*.md` | Document landed behaviour, operator recovery, deployment/downgrade boundaries and actual verification evidence. Update both design statuses only to reflect what passed. |

**Exit:** Run `make check` and `make test-browser`. Run E10 separately only with explicit bounded native authorisation. Use Blender `4.5.16 LTS` in one owned disposable `/tmp/opencode` project, one process at a time, four CPU threads and a 300-second timeout per attempt. Link relative character and motion libraries and use Library Overrides for two independently animated character instances with distinct Actions/NLA timing. Save/reopen, check stable object and unique instance IDs, sample walk, run, stairs up and stairs down, and check contacts, clothing deformation and materials. Run the actual admitted static proxy and `draft_motion` passes through `apps/backend/src/proscenium/previews/blender_worker.py` on the sealed scene. Record source, closure, version, job, sample and map identities, timings, peak composition memory, results and any unverified checks. Capture evidence and clean up only the owned processes and disposable inputs/outputs. No external upload or independent service changes. Without E10 evidence, native acceptance remains open.

**E10 negative case:** Select Hero v3 for interactive authoring while Hero v4 sits at the normal working path. The pre-edit check must stop authoring before any edit and leave v4 untouched. Verify no overwrite, Git reset or sealed snapshot edit. With v4 still at that path, confirm the already-published v3 scene resolves cleanly from its sealed pins.

## Risk & compatibility checklist

- [ ] Preserve historical publications, digests, fingerprints, selections and receipts. Deploy compatible readers/validators before enabling writers of optional fields. Never serialise omitted fields back into historical records.
- [ ] Keep edges, coverage and revision transactional for both writers. Fence backfill, and never describe partial reverse results as proof of no use. Badge-only changes and render ticks do not invalidate topology.
- [ ] Verify owner-only scope, signed cursor binding and exact historical identity on every page. Graph reads must not mutate targeting, drafts, selections or creative approvals, or spend generation budget.
- [ ] Verify old backups untouched, migrate restored copies only and preserve all composed origins. For rollback, disable the new surface/writers first. A binary downgrade needs a compatible verified backup and an explicit plan for newer work.
- [ ] Verify pinned bytes, entry and origin integrity before intent/ref changes, including bounded I/O and replay after uncertainty. Never substitute HEAD or silently relink the owner's working scene.

## Verification gates matrix

| Gate | Wave | Required evidence |
| --- | --- | --- |
| E1 | 1, 2 | Offline structural read and bounded, side-effect-free Outliner expansion. |
| E2 | 1, 2, 4 | Exact shared/historical pins and distinct local occurrences. Wave 4 completes physical inclusion evidence. |
| E3 | 3 | Compatibility, publication/import validation, impact/compare parity and selection isolation. |
| E4 | 1, 3, 4 | Wave 1: publish/import/rebuild parity for existing edge types, rollback/replay, partial coverage and old-bundle restore. Wave 3: publish/import/rebuild parity for `planned_asset`. Wave 4: `includes_source` parity across publication, rebuild and rebuild after normal backup/restore. Reject offline imports that cannot encode multi-commit composed origins. |
| E5 | 1, 2 | Owner scope, cursor drift, stale responses, keyboard navigation and draft isolation. |
| E6 | 2 | Exact historical proxy maps, instance-pair selection and unavailable-map handling. |
| E7 | 4 | Positive pinned composition independent of current working files. |
| E8 | 4 | Rejection, bounds, replay, receipt projection and unchanged legacy digests. |
| E9 | 4 | Selective v4 adoption, fresh restore and unchanged v3 history. |
| E10 | 5 | Separately authorised native Blender evidence, resource measurements and cleanup. |

## Branch/PR delivery sequence

1. Review and merge the documentation-only `docs/version-relationships-spec` PR first. No contract, code or test changes in Wave 0.
2. Branch `feat/version-relationships` from the updated `main`. Review the five waves in order with separate diffs and recorded exit results. Stop on a failed prerequisite and regenerate/check Python and TypeScript bindings, packaged schemas, operation types and router composition with each authorised contract delta.
3. Open the implementation PR with wave-by-wave evidence, recovery/rollback notes and explicit limitations. Merge through branch protection after review and release gates.
4. On an explicitly authorised deployment, settle relevant work, verify a compatible backup and build the merged frontend before restarting `proscenium.service` via the [operator procedure](../operator.md#systemd-user-service). Verify service status, migration/backfill progress and pinned Studio reads. Do not restart independent services or auto-resume held work.
