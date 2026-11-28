# Published relationships, the Outliner and reusable scene inputs

- **Status:** Design agreed for implementation in three increments. Not part of the current runtime contract or an implemented feature. See the [implementation plan](version-relationships-plan.md).
- **Baseline:** `5c0709b`, 28 November 2026. Runtime specification 3.2.0, contract 1.0.0, wire `1.0`.
- **Owner decision:** Keep `asset`, `scene`, `storyboard`, `sequence` and `unity_result`. Skip a dedicated `character` item kind. Use `character`, `motion`, `wardrobe` and similar labels as optional searchable tags.
- **Objective:** Make exact published versions navigable in both directions. Let authors distinguish what a shot plans to use from what a scene actually packages. Do not introduce a mandatory asset hierarchy or a second animation timeline.

## 1. The outcome

A director selects the coat, model and rig in **Hero v3**. An animator adds Walk, Run, Stairs Up and Stairs Down. That need not create four characters, four scenes or four catalogue entries. A reusable performance can be published separately. A one-off performance can remain in the scene. The director can answer three different questions:

1. **Planning:** Which storyboard shots call for Hero v3, including shots with no scene yet?
2. **Production:** Which published scenes declare Hero v3 as an input? Which actually include its source files? Neither fact, on its own, proves the coat is on camera at frame 43.
3. **Change:** If I choose Hero v4 for the stairwell shot, what would I need to revise? Can I still open yesterday’s landing shot with v3?

**Release invariant:** Publishing v4 changes none of the pins, files, selections or previews on old publications. Adoption creates a new consumer version. Viewing a possible change does not perform it.

For review: [current code and risks](#2-review-of-the-current-implementation) · [Outliner and API](#4-navigator-and-read-interface) · [shot intent](#5-storyboards-optional-shot-level-intent) · [source composition](#6-production-compose-pinned-files-at-checkpoint-not-when-browsing) · [release tests](#9-acceptance-tests).

### Scope decisions

| Decision | Consequence |
| --- | --- |
| Keep `asset`, `scene`, `storyboard`, `sequence`, `unity_result`. | `character`, `motion`, `wardrobe` and similar labels are optional, searchable tags. They grant no capabilities. An unrigged sculpt can be a character. A tag does not prove a clip fits a rig. |
| The catalogue is a graph of **versions**. The Outliner is a tree **view from a chosen version**. | A prop can appear under two scenes. Two shots can bind different ranges of the same scene. Neither instance creates another asset or assigns it a single parent. |
| Keep artistic and technical ownership separate. | The storyboard owns ordered shots and panels. Blender owns objects, rig instances, Actions, NLA strips, contacts, cameras and scene time. A sequence owns the editorial arrangement of rendered frames or media, not the character’s skeletal clips. |
| Publish at the granularity of independent review, reuse or revision. | Several Actions inside one `.blend` are fine. Changing that file means a new version of its owning item. A separately versioned motion can change without revising the character. Scene-local modelling and animation never have to become managed assets. |
| Separate browsing from building. | The Outliner is a read. Including a dependency’s files is an explicit authoring operation. A storyboard reference to a character must not hydrate the character just to display the board. |

Not in scope: graph editing or drag-to-reparent, automatic upgrades, a graph database, cross-project links, inferred dependencies from prose or file names, an animation picker/rig compatibility service, animated 3D Inspection or a skinned Unity exporter. No new always-on Copilot graph tool.

## 2. Review of the current implementation

These are findings about the baseline, not descriptions of the proposed behaviour.

| Finding | Evidence and design implication |
| --- | --- |
| Published identity is already sufficient. | [`VersionRef`, `SourceClosure`, `PublishedItem`, `ItemRecord`, `VersionRecord`](../contracts/schema.json#L69) distinguish item, version, source, tags, current head and owner selection. [Publication validation](../../apps/backend/src/proscenium/catalogue/service.py#L217-L276) checks unique version IDs, item-head compare-and-swap and cycles across both dependencies and derivation. No `parent_id` or new item kind is needed. |
| A pin is not a file inclusion. | The [resolver](../../apps/backend/src/proscenium/sources/resolver.py#L105-L191) checks dependency identities but writes only `files[]` to the snapshot. Each file already has its own immutable Git/LFS or retained-source origin. A hand-supplied closure can span commits. The gap is safe composition from published versions, not an inability to represent their bytes. `derived_from` records lineage, not instructions to load a file. |
| Revision and composition are different. | [`checkpoint_sources`](../../apps/backend/src/proscenium/sources/checkpoint.py#L268-L433) can inherit files from **one** `base_version`, replace owned paths and exclude inherited destinations. Dependencies and runtime requirements come from the new request. An inherited Git entry must match the new commit. [Large receipts](../../apps/backend/src/proscenium/copilot/tools.py#L371-L405) have a special projection. Do not turn the base into a list of parents or make the agent build a closure digest. |
| A board already has structure before there is any 3D content. | [`AuthoredShotPlan` and `ShotPlan`](../contracts/schema.json#L408-L430) carry board-wide `asset_dependencies`, stable ordered shots and panels, text motion beats, nullable `scene_version`, inclusive `source_range` and rational `fps`. The [publisher](../../apps/backend/src/proscenium/catalogue/service.py#L329-L385) requires board asset pins and bound scene refs in `source.dependencies`. A panel is an image artefact or an unresolved slot, not a source file. The owner-selected flag is a [separate read projection](../../apps/backend/src/proscenium/catalogue/service.py#L602-L652). There is **no** shot-to-asset field. |
| Existing impact reporting cannot identify the cast of a shot. | [Host `impact`/`compare`](../../apps/backend/src/proscenium/copilot/capabilities.py#L409-L464) treats a board-level asset change as potentially affecting every shot. The [checkout planning helper](../../agent/skills/storyboard/plan_tools.py#L39-L111) duplicates this logic. The [Shots browser](../../apps/frontend/src/features/storyboards/StoryboardBrowser.tsx#L347-L373) shows board dependencies, unresolved inputs, scene binding and panels. None establishes visibility in a shot. |
| The current Outliner has one useful but different job. | [`Outliner.tsx`](../../apps/frontend/src/features/review/Outliner.tsx#L7-L35) lists selectable `(object_id, instance_id)` pairs from a static proxy sample. It cannot navigate dependencies and has nothing to show without a proxy. [Review](../../apps/frontend/src/features/review/ReviewWorkspace.tsx#L240-L290) can open a historical mapping by exact ID. Preserve that behaviour, not just the list’s appearance. |
| There is no indexed, reverse relationship read. | The [catalogue routes](../../apps/backend/src/proscenium/catalogue/router.py#L38-L130) page items/versions and expose separate shot-plan, sequence and proxy projections. A [typed document read](../../apps/backend/src/proscenium/sources/resolver.py#L197-L211) verifies closure files in memory. Scanning every version and opening every storyboard on a UI click is neither a bounded read nor a sensible reverse lookup. |
| There are two version-writing paths and an offline recovery path. | [Live publication](../../apps/backend/src/proscenium/catalogue/service.py#L478-L590) and [owner import](../../apps/backend/src/proscenium/projects/importer.py#L805-L885) each write versions transactionally. [Backup](../../tools/operations/recovery.py#L203-L277) checks reachable commits, including individual origin commits, and local LFS objects. An index maintained only by a UI event listener or one writer will lie after import/restore. |
| Blender evaluation, proxy inspection and Unity export prove different things. | [`RenderSpec`](../contracts/schema.json#L266-L278) selects a scene and frame range, not an Action. The [proxy worker](../../apps/backend/src/proscenium/previews/blender_worker.py#L229-L294) samples one frame, demands unambiguous IDs and exports with `export_animations=False`. The [Unity exporter](../../tools/unity/export_blender.py#L566-L628) samples the evaluated graph, not an animation timeline. It does **not** explicitly set frame 1. It does not emit bones, weights or Actions. Linked-library overrides and two independently animated instances require a native proof, not an inference from the feature tags. |

The [owner policy](../owner-policy.md#1-active-project-scope), [Studio targeting rules](../studio.md#request-targeting-and-references) and [contract compatibility policy](../contracts/README.md#compatible-uat-extensions) apply unchanged.

**Highest-risk implementation points, in order:**

1. **Historical source integrity.** Publication preflight uses [a separate in-memory file verifier](../../apps/backend/src/proscenium/sources/read_only.py#L48-L79). It rejects repeated destinations, whereas the execution resolver accepts repeated destinations if the bytes agree. Produce one unambiguous row per destination **before** either path. Share the new inclusion checks and test both endpoints. Do not make a change to legacy digest rules to solve this.
2. **False negatives in “Used by”.** `Import.commit` does not call `Catalogue.publish`, and a historical source may be unavailable. Update both writers and track coverage. “No uses” is a materially wrong answer if one of those paths was skipped.
3. **Silent loss of new metadata.** `result_text` reconstructs large closures from a fixed key list, and [`_legacy_completed_replay`](../../apps/backend/src/proscenium/sources/checkpoint.py#L656-L686) has an explicit new-field exclusion list. Both need regression tests for `include_sources`. Passing a small happy-path publication proves neither.
4. **Snapshot cost.** The current resolver holds file bytes in memory, and the [preview worker](../../apps/backend/src/proscenium/previews/blender_worker.py#L86-L108) rechecks declared files, including [during frame rendering](../../apps/backend/src/proscenium/previews/blender_worker.py#L468-L477). Benchmark a linked animated scene, not only a catalogue query. Do not weaken source integrity checks to make a benchmark pass.

## 3. What an edge means

Use the following relation names in the proposed read interface. They describe the **evidence** for a link, not an artistic taxonomy. Keys are always scoped to the active project. A published endpoint is an exact `{item_id, version_id}`. A shot or panel also needs the storyboard **version**, not just `shot_id` or `frame_id`.

| Relation (outgoing) | From → to | Authoritative evidence | What it does **not** mean |
| --- | --- | --- | --- |
| `contains_shot`, `contains_panel` | Board version → shot, shot → panel slot | Ordered arrays in the pinned authored plan | A shot or panel is not an independent catalogue item. A missing panel does not imply a missing shot. |
| `board_reference` | Board version → version | `asset_dependencies[]` | Not a cast list for every shot. Preserve historical accepted pins without retroactively narrowing their types. |
| `planned_asset` (phase 2) | Shot → asset version | New optional `Shot.planned_assets[]` | Intent, not proof of binding, appearance, readiness or owner selection. |
| `bound_scene` | Shot → scene version | `Shot.scene_version` and, when known, its range and fps | Not a rendered pass. Shot binding is to a published item, not by guessing a Blender scene name or Action. |
| `declared_dependency` | Published version → version | `SourceClosure.dependencies[]` | A verified version reference. Alone, it does not say why the pin exists or that the referenced bytes were loaded. |
| `includes_source` (phase 3) | Published version → version | Explicit source inclusion verified against the sealed file manifest (§6) | Bytes are present. This is not a list of Blender instances or proof that anything was in frame. |
| `derived_from` | Published version → version | `VersionRecord.derived_from[]` | Creative or production lineage, not a runtime dependency. Show under **Origins** / **Derivatives**, not a count of required files. |
| `sequence_input` | Sequence version → version or media artefact | `SequenceEntry` and contributors (exact version/job/ranges), `SequenceClipEntry` (exact video), or `SequenceAudioTrack` (exact audio) | A rendered clip is not a skeletal motion. A bare entry `shot_id` does not identify a board version. |

Panels expose the authored role, composition, state and nullable `artifact_id`. Display availability and owner choice separately from that authored state. Unbound shots, absent images and unresolved notes are useful information, **not fabricated target nodes**. Brief images, result artefacts and jobs remain available through their existing dedicated views. This change does not build a universal media or job graph.

In particular, even a successful scene render does not establish **which library supplied this mesh** or **which Action ran on this rig at this frame**. Answering those questions would need a version-to-instance binding and frame-specific evidence from the native evaluation. Neither `ProxyMapping` nor the proposed graph contains that evidence. Do not join the two by object or Action name.

Specific evidence takes precedence in the display: a shot binding also required in `source.dependencies` is not a second independent consumer. Keep the underlying pin, group it under the more informative relation and retain every distinct shot/entry use. Board-wide and shot-local declarations can both be true. Never collapse `derived_from` into a runtime edge. Legacy refs with no more specific evidence remain **Declared dependency**. Inverse relations answer *who refers to this exact version?*, not *who would definitely break?* To find indirect use, follow Hero v3 → consumer scene → shots bound to that scene. Show the path, not an invented Hero-to-shot edge. If showing totals, label distinct consuming versions separately from shot/entry occurrences.

## 4. Navigator and read interface

### Interaction

- **Assets** finds items and version histories. **Shots** remains the working surface for timing, panels, owner choices and feedback. **Outliner** follows relationships from any viewed published version, even an asset with no storyboard or proxy. With no viewed version, offer a project-local item/version picker, not an implied project root. With no publications, explain that there is nothing to navigate.
- First view: the exact root name and version, **Uses** and **Used by**. Group structural children, reference/production inputs and provenance separately. Outgoing shots and panels keep authored order. Incoming results include **historical** consumers by default, with current/owner-selected badges clearly distinguished from the version being viewed. Do not substitute “latest”.
- Expand one node at a time. Repeated references on different paths link to the same version. Guard against cycles on the current traversal path, not with a global visited flag that hides a valid second use. Two uses of a rig in one `.blend` remain **one** version dependency unless there is separate evidence for two instances. Local scene objects do not appear as catalogue assets.
- Expanding, collapsing, filtering or switching direction does not change Review, Composer target, references, saved owner choice or pending draft. **Open version**, **Open shot in Shots** and **Inspect panel** are explicit navigation using existing pinned navigation. A later **Adopt** action would be a separately requested edit, never a click on a dependency. Preserve keyboard navigation, focus, back navigation and per-project isolation. Cancel stale reads on root/project changes or collapse.
- Move the existing object selector to **3D Inspection → Objects at frame N**. Use the viewport’s **active mapping**, including `mapping_artifact_id`, `proxy_artifact_id`, `sample_frame`, version and digest. Preserve Ctrl/⌘ multi-select and stable IDs, keep object context distinct from a spatial hit, and clear incompatible selections on mapping changes. Do not fetch a different “latest” mapping for the list. If the original is unavailable, say so. No Three.js import or proxy/media request just to open the relationship view.

Illustrative display, not a prescribed project structure:

```text
Stairwell escape · storyboard v2
  Board references: Stairwell design · v1
  Shot 01 · Cross the landing
    Planned: Hero · v3
    Bound scene: Landing · v2, frames 1–48 at 24 fps
      Includes source: Hero · v3, Walk · v1, Staircase · v1
  Shot 02 · Run down the stairs
    Planned: Hero · v3       Scene: not yet bound
    End panel: selected      Motion beat: “break into a run”
```

No `Run` asset is inferred from that text. A selected panel is not a completed render. From **Hero v3 → Used by**, distinguish *planned for Shot 02* from *included by Landing v2*. Another board using the same shot ID must never appear as the same shot.

### Proposed owner read contract

`GET /api/v1/projects/{project_id}/versions/{version_id}/relationships` (`getVersionRelationships`). Same owner authentication and active-project checks as the catalogue. No tool/worker token on this owner route. It returns **one hop**, not an unbounded nested tree.

| Input | Meaning |
| --- | --- |
| `direction` | `uses` (default) or `used_by`. Inverse results keep canonical edge orientation. |
| `shot_id` | Optional. Changes the root to this shot **in the requested board version**. Wrong kind, unknown shot or unavailable historical evidence are distinct outcomes. |
| `limit`, `cursor` | Default 50, valid 1–100. Opaque signed cursor bound to project, exact root, direction, sort and relationship revision. No offset across changing results. |

`RelationshipPage` is a closed wire record: `schema_version`, `project_id`, `root`, `direction`, `relationship_revision`, `coverage`, `items[]`, `nodes[]`, `next_cursor`. `root` is `version{ref}` or `shot{board, shot_id}`. Edge endpoints may also be `panel{board, shot_id, frame_id}` or `artifact{artifact_id}`. Tags denote variants, not ID prefixes. Each row has `relation`, `from`, `to` and a **relation-specific** `details` record, not arbitrary client-supplied properties. `nodes[]` contains one summary per distinct endpoint on this page plus the root: identity, display label (fall back to the real ID), and kind-specific metadata. Version summaries carry the immutable kind/name and current/owner-selected badges. A shot summary carries intent and whether its binding is recorded, unbound or unknown. A panel summary carries its authored state and actual choice/availability status, never an invented image. These summaries need no per-row metadata fetch. Counts, badges and mutable selection overlays are not part of an immutable edge’s identity.

Details include authored order for structural rows, panel slot metadata for `contains_panel`, nullable range/fps for `bound_scene`, and entry/track ID, role, actual job and ranges where applicable for `sequence_input`. Deduplicate by the full typed tuple including local slot or contributor identity. Never key by display name, path-string concatenation or coincident `shot_id`. Sort by fixed relation-group order, authored ordinal where meaningful, then a deterministic identity tie-breaker. Reverse lookup orders by pinned consumer and local identity. Panel and media nodes can open their existing detail surfaces. No reverse media search is required in this release.

Example row from expanding Shot 01 (IDs are illustrative):

```json
{
  "relation": "bound_scene",
  "from": {"kind": "shot", "board": {"item_id": "escape", "version_id": "escape-v2"}, "shot_id": "landing"},
  "to": {"kind": "version", "ref": {"item_id": "landing-scene", "version_id": "landing-v2"}},
  "details": {"source_range": {"start": 1, "end": 48}, "fps": {"numerator": 24, "denominator": 1}}
}
```

`coverage` reports `state: complete | partial` **for this query**, and zero or more reasons (`indexing`, `source_unavailable`, `invalid_historical_record`). `complete` means all declared relationships in scope were indexed, not that Blender used them, files still exist, or there are no unpublished consumers. End of pagination (`next_cursor: null`) is independent of coverage. Only a complete, exhausted, empty reverse query may say **No published uses of this version**. Otherwise say what could not be checked. Never turn a timeout, revoked session or failed page into an empty tree.

Return `200` with known rows and `partial` for incomplete indexing of an existing root. Return `404 not_found` for an unknown in-project version or a shot known not to exist on that board, `403 scope_denied` for an inactive project/wrong authority, `400 invalid_request` for a shot filter on a non-board, and `400 invalid_cursor` for a malformed or wrong-scope token. Use `401` for an unauthenticated owner read. A changed relationship revision is a separate `409 relationship_revision_changed` (proposed code). Report valid values or the next safe action. Do not echo tokens, the full cursor or filesystem contents in errors.

### Index and consistency

1. Build a disposable per-project SQLite read index over published versions and canonical storyboard/sequence documents. Use indexed outgoing and incoming keys, a local-occurrence key, per-version coverage and a monotonic project relationship revision. Use the existing [Store migration owner](../../apps/backend/src/proscenium/db/store.py#L141-L167), not another database, editable manifest or machine-wide asset catalogue.
2. Use **one pure projector** on validated evidence. Live publication and offline import insert rows and advance the revision in the same project transaction as versions, after validation and before commit. Rollback or idempotent replay contributes no rows or new revision. Store enough structural data for unbound shots and missing panels. Do not persist mutable owner selections as if authored. Read those from the existing selection projections when displayed.
3. Backfill existing active-project versions in bounded, restartable batches outside GETs and outside long write transactions. First index `VersionRecord` pins and lineage. Extract slot-specific evidence from exact sealed, verified documents, never the working tree. A missing LFS object can leave the generic pin known but its shot attribution unknown. Track that distinction; an incomplete producer can make a reverse answer partial. A repaired source or a rebuilt index can improve coverage without rewriting the publication. Fence rebuilds against concurrent publication/import; never replace newer rows with an older scan.
4. Page by indexed keyset with `limit + 1`, in one consistent read transaction. No `tx.all("versions")` on the hot path, per-item HTTP fan-out, Git/LFS reads, snapshot creation, native launches or provider probes. Verify the pinned identity before returning labels. A signed but superseded cursor returns `409` with a restart-first-page instruction, not a mixed-generation page. Unrelated render-frame ticks do not invalidate topology. Invalidate visible graph reads on `publication.created`, project reactivation and reconnect. For a partial result offer **Refresh relationships**: reread the current revision, do not start a rebuild, poll in the background or impersonate a new publication. Selection/media-status updates refresh badges, not the graph topology.

## 5. Storyboards: optional shot-level intent

Add **one optional authoring field**: `Shot.planned_assets: VersionRef[]`, unique by exact pair. Add it both to the authored `Shot` definition **and** the separate nested shot record in the projected `ShotPlan`. There is no mandatory cast record and no rig requirement. For this new field the target must exist in the active project, belong to the supplied item, and have `kind: "asset"`. Do not change validation of historical board-wide refs.

```json
{
  "shot_id": "stairs-down",
  "intent": "Reach the exit before the door closes",
  "camera_intent": "Hold on the lower landing",
  "motion_beats": ["Break into a run, then descend the stairs"],
  "planned_assets": [{"item_id": "hero", "version_id": "hero-v3"}],
  "scene_version": null, "source_range": null, "fps": null,
  "frames": [{"frame_id": "stairs-end", "role": "end", "composition": "Hand on the exit door", "artifact_id": null, "state": "missing"}]
}
```

- Missing field = not recorded. `[]` = no pins declared here. Neither means “no character appears”. Unknown future assets stay in `unresolved` or creative prose, not placeholder `VersionRef`s. Allow shots to share pins or deliberately refer to different versions of the same item. A planned pin differing from a bound scene’s pin is worth displaying, not a publication error.
- `asset_dependencies` remains the board-wide set, **not** a roll-up of the shot lists. At publication, the union of board-wide assets, all shot-local planned assets and non-null scene refs must be present in `SourceClosure.dependencies`. Dedupe the union. Other declared refs remain permitted. Do **not** copy shot-local refs into `asset_dependencies` or fetch their bytes. A new board with all shots unbound can be published, viewed and revised without any renderer.
- Run identical identity/kind checks in standalone plan validation, publication preflight, actual publication, read projection and offline import. Enforce membership in the closure wherever a publication/closure is present; a standalone plan has no closure yet. Existing inline batch publication can resolve same-batch planning refs. Checkpoints resolve already published refs, so publish producers first when using their pins in a checkpoint. The authored file never stores `source_commit` or `owner_selected`. An owner choice is scoped to the published board version and frame slot, not transferred to a revision.
- Extend `impact` and `compare`: a changed asset pin local to shots 1 and 4 affects those shots, not all shots. A board-wide pin still conservatively affects all. Check both versions of a revised plan for removal/replacement; preserve existing order-change and global-change behaviour. Treat the new list as a set for impact (permutation or omitted versus empty alone has no creative impact). Existing `changed_ids` are untyped: retain conservative item-ID matches and report *potential impact*, never claim measured visibility. Delegate the checkout helper to shared pure logic or enforce fixture parity. Impact reports do not adopt, rerender or rewrite anything.

## 6. Production: compose pinned files at checkpoint, not when browsing

### Additive contract

| Record | Proposed change |
| --- | --- |
| `CheckpointRequest` | Optional `include_sources: VersionRef[]`, requiring `entry_path`. Each ref is a distinct, **already published**, same-project version. A caller supplies pins, not hashes or a list of files copied from HEAD. `paths` still means the non-empty set of files owned by this checkpoint. |
| `SourceClosure` | Optional `included_sources: VersionRef[]` records the effective **direct** sources whose complete file manifests were included. It is a subset of `dependencies`. `files[]` remains the complete, flat, independently resolvable manifest. Omit `included_sources` when empty. |
| `PublishedItem` / `VersionRecord` | No new item kind, parent, cast or animation fields. The new closure metadata travels inside the existing source/receipt. Both inline and `checkpoint_operation_id` publication must enforce the same inclusion semantics. |

Example **checkpoint fields**, not a complete tool request:

```json
{
  "entry_path": "scenes/landing/landing.blend",
  "paths": ["scenes/landing/landing.blend"],
  "include_sources": [
    {"item_id": "hero", "version_id": "hero-v3"},
    {"item_id": "walk", "version_id": "walk-v1"},
    {"item_id": "staircase", "version_id": "staircase-v1"}
  ],
  "required_runtime": {"application": "blender", "version": "4.5.16 LTS", "features": []}
}
```

The resulting scene manifest lists its own `.blend` from the scene commit and the libraries/files at **their original destinations, commits and hashes**. It contains the three `VersionRef`s once in each applicable set. No source binary is copied into another Git history by this operation. Materialising a private job snapshot still consumes space for the actual bytes. This is not a deduplicated snapshot-store redesign.

Example file ownership, not a required folder convention:

| File in the active project | Owner and use |
| --- | --- |
| `assets/hero/source/hero.blend`, `assets/hero/source/human.hero.json` | Hero v3 owns the dressed rest-pose character, rig, packed images and optional MPFB authoring preset. |
| `assets/walk/source/walk.blend` | Walk v1 independently owns the reusable Action in this example. A single locomotion asset containing four named Actions is equally valid. Raw motion-only FBX can be retained as source, not a rendered artefact. |
| `assets/staircase/source/staircase.blend` | Staircase v1 owns the reusable geometry. A scene-local staircase would need no published asset or graph edge. |
| `scenes/landing/landing.blend` | Landing v2 owns its camera, layout, rig overrides and timed performance. Its closure contains the linked inputs; its editable file need not embed a copy of the body. |
| `storyboards/escape/plan.json` | The board owns intent, ordered shots and panel **references**. Registered panel images and render outputs stay in the artefact store. No `.blend` is needed to read the plan. |

### Deterministic merge rules

1. Resolve exact refs, validate each producer’s identity, closure digest, origin and permitted paths. Take only the explicitly included producer’s **sealed `files[]`**. Those files may already contain that producer’s included sources. Do not recursively traverse `dependencies` or `derived_from`. No filesystem-latest, name matching, network fetch, automatic installation or worktree content substitution.
2. With no base, start from no inherited files. With a legacy base, inherit its files as today. With a composed base, separate its previously included destinations from base-owned destinations using its immutable `included_sources` manifests. If the new request omits `include_sources`, **retain** the base’s direct source inclusions. If supplied, treat it as the complete replacement set; `[]` detaches them all. Rebuild included destinations from that set, dropping files that only the old inputs supplied. Preserve other base-owned files except explicit exclusions/replacements. Do not infer ownership of old files from names or identical bytes.
3. Union the new request’s ordinary `dependencies` with the effective inclusions. Do not inherit unrelated metadata pins, ancestry or `required_runtime`. Preserve today’s entry rule: a Git entry must be pinned to the consumer’s `source_commit` (including validated re-pinning of an unchanged owned entry). A dependency cannot supply or overwrite the consumer’s entry. Existing retained-source entry semantics do not change.
4. Preserve project-relative destinations. No prefix stripping, arbitrary remapping or automatic Blender path rewriting in this release. Two includes may share one destination only if their full `ClosureFile` rows are identical, including origin. Differing origins, even with equal bytes, are a reported conflict rather than an arbitrary provenance choice. Reject file/directory prefix conflicts and any collision between an effective import and a local/inherited-owned/retained file. No last-writer-wins. `exclude_paths` cannot mask part of an active inclusion. For a legacy base with a hand-copied library, explicitly exclude that **old inherited row** when converting to an inclusion: exclude is applied to the base-owned lane before merging and never removes a newly imported row. Existing exclusion-versus-supplied-local conflicts still fail.
5. Consumers either import the producer’s whole sealed manifest or only reference the version. Publishers needing a lean distributable can make a separate asset. Partial file picking, two versions mapped onto one path and automatic library relocation are deferred. Two revisions of one item with disjoint paths need not be rejected just because they share an item ID.
6. Validate scope, local availability (including LFS and retained bytes), collisions, total expanded file/byte limits and the entry rule **before** creating a checkpoint intent or moving Git refs. Verify again during recovery/commit as today. Use the existing safe path, Git object, LFS hash and retained-record checks. Never stage included files in the owner’s index, update their working copies or ask the agent to read the private DB. Registered panel/render media is not `RetainedClosureFile`. Proposed initial admission bounds for the new composition path: 128 direct includes, 4,096 distinct expanded destinations and 512 MiB total hydrated bytes. Count shared rows once, check declared sizes before bulk reads, verify actual size/hash with bounded I/O. Benchmark these limits before enabling; do not impose them retrospectively on historical closures.
7. Freeze the effective pins, original origins and deterministic ordering for replay. Sort new unordered ref lists by `(item_id, version_id)` before fingerprinting new requests and before computing a new closure digest. Duplicate refs within either input list are invalid; overlap between a dependency and inclusion is normal. In a request, omitted and `[]` are distinct when the base has inclusions. Preserve legacy fingerprints, digest bytes, compact/large receipt handling and replay after lost acknowledgement. Never allow the legacy replay fallback to ignore a new option. A genuinely unknown checkpoint outcome remains unknown.
8. Reject a fabricated inclusion even with a valid-looking digest on an inline closure: exact direct versions must exist, be in `dependencies`, and their manifest rows must be present unchanged. Use the same composition checks in `validate_authored_document`, `publish_outputs`, `checkpoint_sources`, read verification and applicable import paths. Use the existing cycle checks over version refs including `derived_from`. Read verification checks immutable records/manifests; it must not recursively stage any producer. Readers of legacy records never synthesise `included_sources` or reinterpret old pins as runtime inputs.

### Authoring is not materialisation

Blender resolves a relative library path from the **referencing `.blend`**, not from a catalogue ID. Store a portable relative library reference, not an absolute workstation path or `.proscenium/snapshots/<run>/…` path. Its resolved destination, including nested libraries/textures, must land at the verified file in the sealed snapshot. A native `//../../assets/…` reference may resolve in scope; a closure destination itself is a validated project-relative path, not a Blender path expression.

The live bridge can otherwise open Hero v4 from the working tree while a receipt correctly pins v3. For interactive authoring, compare the actual library files used against the selected published origins **before editing**. If they cannot be verified or differ, stop that authoring step. Do not overwrite the owner’s v4 file, reset Git, or edit a sealed snapshot. Checkpointing an already-authored portable scene and rendering its historical pin must work with v4 or no library at all at the normal working path. This specification does **not** claim it provides an interactive historical editing workspace. A host-prepared, project-owned scratch workspace with verified pins and safe relinking is a separate follow-up if that workflow is required.

## 7. Skills, adoption and failure behaviour

| Surface to revise **when the feature ships** | Required guidance |
| --- | --- |
| [`storyboard`](../../agent/skills/storyboard/SKILL.md), [template](../../agent/skills/storyboard/templates.md), [`hero-assets`](../../agent/skills/hero-assets/SKILL.md) | Keep pitch/shortlist and unresolved needs possible without publications. Use board-wide pins only for board-wide intent, `planned_assets` for evidenced shot intent and real scene refs only when bound. An asset may derive from board v1 and be planned in board v2 without mutating v1 or forming a same-version cycle. |
| [`blender-authoring`](../../agent/skills/blender-authoring/SKILL.md), [`mpfb2`](../../agent/skills/mpfb2/SKILL.md) | Publish the clothed, weighted character once; pack required textures. For reusable performances retain named Actions (actual users/NLA strips or deliberate retention) and verify save/reopen. The scene composes the performance for each rig instance. Use a library override or another proven non-destructive binding where needed, not a prescribed filename or duplicated character. Retain all MPFB root, preset, provenance and temporary-object safeguards. |
| [`mixamo`](../../agent/skills/mixamo/SKILL.md), [`research`](../../agent/skills/research/SKILL.md) | Distinguish original motion-only FBX, retargeted Action, runtime-linked library and rendered video. A clip baked into the scene does not require the raw FBX at render time. Retain source/motion IDs, rig/rest pose, frame rate, range, loop and root-motion choice where known. Keep exact upload approval and source attribution. Tagging an input is not validation. |
| [`unity`](../../agent/skills/unity/SKILL.md), sequence and media workflows | `run_unity_workflow` is still the admitted evaluated-mesh path. No Humanoid Avatar, skinning, animation transfer or Action switching is implied. `assemble_sequence` works on receipt-backed frames or ready media, not Blender Actions. Generation skills continue to use their existing preparation and admission tools. |
| [Native routing](../../apps/backend/src/proscenium/copilot/native.py#L41-L99), [tool schemas](../../apps/backend/src/proscenium/copilot/tool_schemas.py), [guidance](../../apps/backend/src/proscenium/guidance.py) | Keep instructions short. Continue using `get_project_capabilities`, `list_items`, `get_version`, `validate_authored_document`, `checkpoint_sources`, then `publish_outputs`. Pass the completed receipt directly or its operation ID and matching commit. No model-side closure flattening, no owner GET via borrowed credentials. Test portable model-schema projection for the added optional fields. Do not widen default worker permissions. |

**Explicit adoption example.** Publish Hero v4 with the revised coat. The Outliner still shows Landing v2 → Hero v3, even if Hero v4 becomes the current or owner-selected version. To adopt v4 **only** on the stairs, revise the stairwell scene, pin its prior version as `base_version`, replace just its Hero inclusion, keep the other effective inclusions and test the rig, deformation and stair contacts. Checkpoint against the observed Git `expected_head`, then publish with the receipt's `source_commit` and the item's observed `expected_current_version_id`. A board which should use that take needs its own revision updating the corresponding shot binding (and planned asset only if intent changes). An existing sequence still points to its original inputs. On a head conflict, show the competing versions; no cascade or forced update.

**Feedback and boundaries:** `planning_only` permits discussing the graph and authoring an explicitly requested plan, not rendering or scene execution. Request references, browsing, owner selection, source pins and approval for external sharing remain different records. Reads must not touch credentials, siblings, services, native processes or the generation budget. Use existing error envelopes and reviewed, static model-safe reasons. Examples of the required remedies:

| Condition | Required result |
| --- | --- |
| Invalid direction, invalid target kind or duplicate planned pin | `invalid_request`/validation issue at the field or array index. State valid directions (`uses`, `used_by`) or the required `asset` kind. Identify the offending safe pin, not a private path. |
| Foreign project, item/version mismatch, missing ref or missing pin in closure | Refuse out-of-scope input without exposing other projects. For an in-scope missing pin identify the consuming shot/version and the exact `VersionRef` to add or correct. |
| Two inputs claim the same destination differently | `dependency_collision`. Identify the safe relative destination and conflicting in-project refs. Suggest changing the owned path or publishing a non-conflicting library. Never silently prefer HEAD. |
| Missing/corrupt historical Git/LFS/retained source, or size limit exceeded | Name the in-scope missing origin or failed bound and next action: restore the verified local object, or publish a smaller input. No silent download, media substitution or success receipt. |
| Coverage incomplete, stale cursor or unavailable proxy | Distinguish **relationships not fully indexed**, **refresh this page** and **no verified proxy for this sample**. Missing proxy is not a graph error. A broken page never strands the owner’s review draft. |

## 8. Delivery and compatibility

| Increment | Changes | Exit condition |
| --- | --- | --- |
| 1. Read-only Outliner | Owner route, closed page/node/edge definitions, reversible derived index and backfill, exact forward/reverse UI, object selector in 3D Inspection. Read only **existing** publication/plan/sequence semantics. | Historical boards, scenes without proxies, imported projects and shared assets browse correctly. No change to publishing rules, existing field meanings or source digests. |
| 2. Per-shot planning | Add `planned_assets` to both shot schemas, publisher/importer, impact/compare, Shots, Outliner and skills. | A pin local to two shots is shown and reported on those two shots. Old boards and owner selections still open byte-for-byte unchanged. |
| 3. Pinned file composition | Implement checkpoint merge, inline/receipt validation, source-size guards, replay/recovery, `includes_source` and updated authoring guidance. | Disposable multi-library historical scene and native proof below pass. Until this ships, use **Declared dependency**, never “included” based on a pin alone. |

For each increment, update normative `docs/contracts/schema.json` and `openapi.yaml` only for authorised interface changes, document additive behaviour in `docs/contracts/README.md`, run `make generate` and check packaged schemas, Python/TypeScript bindings, operation types and FastAPI router composition together. Advertise the landed feature, not this draft. No work/job lifecycle transition or new creative approval type is proposed. Update `docs/studio.md`, `docs/specification.md` and operator recovery guidance when behaviour changes.

Historical publications, source digests, selections, context fingerprints, backup manifests and checkpoint receipts are not rewritten. Omitted new fields are not serialised as defaults into historical records. Deploy readers/validators before writers. A pre-change closed-schema reader cannot be assumed to accept records with new fields. Database migration must use normal Store backup/version guards; a previous binary can refuse a newer DB. Roll back the surface/writers first; a full binary downgrade requires a compatible verified backup and explicit handling of newer work, never deleting it to fit the old schema. Import existing manifests as before. If the offline manifest does not support composed origins yet, reject that new representation explicitly rather than flattening every origin to the importer’s single commit. A normal backup/restore **must** preserve composed versions without this restriction.

In particular, [`recovery.existing_store`](../../tools/operations/recovery.py#L138-L148) currently requires `PRAGMA user_version == len(MIGRATIONS)`. Adding an index migration is therefore also a **restore compatibility change**. Verify the untouched old bundle with its supported format, migrate only *new restored copies* of its project and registry stores through the supported version path, and rebuild the projection there. Never migrate the backup in place or weaken checks for an unknown future database. Include a pre-change backup fixture in the phase 1 gate, not just a backup taken by the new binary.

## 9. Acceptance tests

Baseline verification for this review: 177 focused checkpoint, import, targeting, spatial and skill tests passed. `make check` passed on the unchanged implementation (2,076 Python tests and 214 frontend unit tests). Those numbers do **not** validate the proposed behaviour. Browser and native journeys below were not run for this specification.

All fixtures below are disposable. These are **release requirements**, not claims that the proposed features have run.

| Test | Passing observation |
| --- | --- |
| **E1. Graph without production** | With no Blender, proxy, panels or providers, a published board opens at its exact version and lists ordered shots, missing panels and unbound scenes. Expanding one node causes at most one paged relationship request, no media downloads, GET-time index writes, Git/LFS access, snapshots, jobs or change in work state. |
| **E2. Shared reuse and pins** | Two boards share a scene and two scenes reference Hero v3. Forward and reverse queries distinguish board, shot, lineage, declared dependency and physical inclusion. Current Hero v4 does not stand in for v3. Historical version labels, role-specific counts and duplicate shot IDs across boards are correct. A standalone character has no required board. |
| **E3. Storyboard and impact** | Test legacy missing field, explicit empty, two shot-local pins, global+local duplicates, unknown/mismatched/wrong-kind refs, missing union pin, changed/removed/reordered shots and publication batches. New shot-only refs never become global. Changing a global pin remains conservative. Placeholder motion text does not create an asset. Owner-selected panel evidence on v1 is not an approval on v2. |
| **E4. Index reliability** | New publish, owner import and rebuild yield identical typed edges. Failed batch, replay, restart and interrupted backfill yield no partial *committed publication* or duplicate edges. With a historical document unavailable, return known pins and explicit partial coverage; never claim an empty complete reverse result. Repair and resume without changing the historical version. |
| **E5. Navigation and access** | Test inactive/other project and wrong caller channels, swapped identities, signed cursor reuse across roots/directions/projects, graph change between pages, and late responses after project change. Hidden/collapsed graph does not refetch on render floods. In keyboard-only navigation, expansion does not send a request to Copilot, alter explicit Composer targets, create a selection or discard drafts. Explicit Open uses the exact version. |
| **E6. 3D Inspection regression** | In a version with two proxy maps, reopening an old pin shows the matching sample’s object list, persistent pair and multi-selection. If its map is unavailable, no substitute hit is fabricated. A scene with no proxy still has working relationship navigation. |
| **E7. Positive composition** | Publish Hero v3, Walk v1 and Staircase v1. Advance or remove their ordinary working copies. Checkpoint a scene against the older origins. The consumer receipt includes exactly the pinned bytes, owned entry commit and dependencies; unchanged binary inputs are neither staged nor recommitted. Metadata-only storyboard references materialise no model files. Test transitive sealed files, shared identical origins, retained source and local LFS, small inline and large receipt-backed publication. |
| **E8. Negative composition and recovery** | Reject conflicting origins, file/directory collisions, unsafe/reserved paths, wrong repository IDs, damaged retained bytes, missing LFS objects, fabricated inline inclusions, stale heads and over-limit requests. No new Git ref/intent on invalid initial input. Simulate acknowledgement loss on either side of ref update, preserve unrelated staging, verify same-key replay and changed-input conflict. Omitted, empty and replacement inclusion lists behave as specified on legacy and composed bases. Legacy digest and receipt fixtures still match. |
| **E9. The wardrobe revision** | Adopt Hero v4 only in Stairwell v3 and one later board revision. Source files removed in v4 do not linger from v3. Landing v2, the old board and sequence still resolve against their old pins. Restore into a fresh private destination with no workspace HEAD fallback and repeat. The Outliner shows both histories. |
| **E10. Native, separately authorised** | In one owned disposable Blender 4.5.16 LTS project, verify the relative character and motion library links, save/reopen, stable object and unique per-instance IDs. Exercise two character instances with different Actions/NLA timing and walk/run/up/down, including feet against stair treads and clothing deformation. Sample distinct poses and check materials. Run the actual admitted static proxy and `draft_motion` paths against the sealed scene, not just an interactive screenshot. Use a bounded CPU allocation, e.g. one process at a time, four threads and a 300-second timeout per attempt. Record source/closure/version, job, sample and map identities, actual findings and unverified checks. No external upload or independent service changes. A successful still is not proof of motion, proxy identity or Unity animation. |

Performance gate: use the existing [Studio performance fixtures](../performance-plan.md) plus a synthetic project with 1,000 versions, 10,000 edges and more than 100 direct consumers of one version. Instrument request count, SQL/query plans and bytes read. One query page must be bounded by its page size and indexed lookups, not total project history. Traverse all pages at a stable revision with no gaps or duplicate edges. Record cold/warm timings and peak composition memory on the reference workstation; no fabricated timing target or “fast enough” based on a tiny board.

Relevant existing suites include [checkpoint/replay](../../tests/test_checkpoint_closure.py), [publication/preflight](../../tests/test_worker_capabilities_and_publication.py), [owner import](../../tests/test_project_importer.py), [request targets](../../tests/test_request_targeting.py), [proxy maps](../../tests/test_spatial_annotations.py), [first-open UI](../../tests/browser/storyboard-first-open.spec.ts) and the [MPFB](../../tests/test_mpfb2_skill.py) / [Mixamo](../../tests/test_mixamo_skill.py) skill checks. Add executable service/contract and browser scenarios for E1–E9; skill prose assertions are not a substitute. Run `make check` and `make test-browser` for the implementation. Keep E10’s native proof separate from hermetic CI.

**Review decisions:** Approve the three increments independently. The recommended defaults are a read-only, all-history, one-hop Outliner; optional per-shot intent rather than a character type; and whole-manifest source inclusion with no path remapping. Revisit historical *interactive editing* and partial file imports only if a real project cannot be authored safely or within the measured bounds. Neither is required to browse a storyboard or reopen an already published scene.
