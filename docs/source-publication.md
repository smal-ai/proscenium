# Source resolution and publication

**Normative for contract 1.0.** Record shapes are in [the schema](contracts/schema.json).

## 1. Canonical sources and immutable versions

Each active project has a dedicated Git working tree. V1 project registration requires its root to be the working-tree root, rather than a subdirectory of a repository containing other projects. A Unity project lives beneath that root. Trusted Git tooling may access the repository's object storage, but agent-visible content remains project-scoped.

### Project workspace bootstrap

When `POST /api/v1/projects` runs with `create: true` (or initialises an unbootstrapped workspace root), it sets up the workspace idempotently before returning `ProjectRecord`:

1. Create project directories `brief/`, `assets/`, `scenes/`, `storyboards/`, `sequences/` and `.proscenium/`, and write `project.json` (`ProjectConfig`).
2. Run `git init -b main` if `.git/` is absent, then run `git lfs install --local`.
3. Write or ensure `.gitignore` contains:
   ```gitignore
   .proscenium/
   .env
   .env.*
   !.env.example
   .envrc
   .direnv/
   *.blend1
   *.blend2
   [Ll]ibrary/
   [Tt]emp/
   [Ll]ogs/
   [Bb]uilds/
   [Uu]serSettings/
   [Mm]emoryCaptures/
   ```
4. Write or ensure `.gitattributes` tracks binary authoring and media formats in Git LFS so agent checkpoints never commit raw binaries into standard Git objects:
   ```gitattributes
   *.blend filter=lfs diff=lfs merge=lfs -text
   *.fbx filter=lfs diff=lfs merge=lfs -text
   *.glb filter=lfs diff=lfs merge=lfs -text
   *.png filter=lfs diff=lfs merge=lfs -text
   *.jpg filter=lfs diff=lfs merge=lfs -text
   *.jpeg filter=lfs diff=lfs merge=lfs -text
   *.webp filter=lfs diff=lfs merge=lfs -text
   *.exr filter=lfs diff=lfs merge=lfs -text
   *.hdr filter=lfs diff=lfs merge=lfs -text
   *.mp4 filter=lfs diff=lfs merge=lfs -text
   *.tga filter=lfs diff=lfs merge=lfs -text
   ```

Copilot edits canonical filenames and checkpoints a coherent, completed change set. A checkpoint records the files and dependencies required by that result. The tool checks staged/working-file ownership before committing and preserves unrelated staging. Uncommitted or dirty input bytes cannot masquerade as the contents of the declared commit.

Version identity is opaque and stable. Source identity combines full commit SHA, canonical path, actual content SHA-256 and a complete materialisation manifest. File modification times, branch names, Git HEAD at review time and model-generated version labels are not source identities.

## 2. Dependency materialisation contract

A `SourceClosure` contains an entry path, all files needed to open/render it and pinned item-version dependencies. Each file maps a **materialised project-relative path** to either:

- `git`: repository ID, full commit, source path and expected hydrated content hash; or
- `retained`: a project-owned immutable retained blob and its hash, for permitted local-only content which must not enter Git/LFS.

Every origin belongs to the active project. Referencing another project or a mutable absolute path is rejected. A local-only source requires a durable retained copy before publication and is included in the project's backup manifest.

### Resolution algorithm

1. Validate every relative POSIX path. Reject absolute paths, `..`, empty components, NULs and reserved private/runtime paths used as source destinations.
2. Resolve the declared repository/retained-store identities through the project registry, not a model-supplied filesystem root.
3. Read each Git file blob from its exact commit, rejecting a tree/directory in a file slot. If it is an LFS pointer, resolve the corresponding object and verify the **hydrated content** hash. Missing LFS data yields `source_unavailable`; it does not trigger an implicit network fetch/push.
4. Resolve retained blobs by ID/hash. Missing or changed local-only content yields `retained_source_missing`.
5. Create a new ignored materialisation directory using the closure digest. Copy bytes or use copy-on-write storage. Never use writable hard links to the live source or shared retained blobs.
6. Deduplicate identical destination/hash pairs. Reject two different hashes mapped to the same destination as `dependency_collision`. The agent must author a valid packed/appended source or distinct versioned dependency paths. Do not select whichever file was encountered last.
7. Rehash the populated tree and seal its receipt before admitting a render. The renderer receives this root, not the live Git working tree.
8. Reopen the entry source through the relevant tool and verify its resolved dependency closure. Relative references must resolve inside the materialisation. Undeclared dependencies produce `dependency_missing`; the renderer may not read a coincidentally available live asset instead.

Copilot chooses how to author valid Blender references. The resolver checks that the declared bytes can actually be presented together. Multiple versions can coexist only where the authored source has unambiguous paths or embedded content. The same canonical working filename can appear in different materialisations without creating numbered working sources.

The closure digest is SHA-256 of RFC 8785 canonical JSON for the manifest excluding its own digest. File rows are sorted by materialised path; dependency rows by item/version ID. Include entry path, file hashes, pinned dependencies and required runtime/features. A renderer adds its own versioned recipe/profile fingerprint to form the render input identity.

## 3. Stable object and instance identity

Blender tooling persists a project-scoped object ID on each authored object. Renaming preserves it. Duplication/replacement creates a new instance/object ID with optional `derived_from` provenance; a name or glTF node index cannot substitute for that ID.

The proxy mapping records source object ID, instance ID, node path, sampled frame and source closure digest. Linked/collection instances need persistent tool-authored IDs. If a topology change makes identity ambiguous, publish new identities and retain old annotation targets. Do not fabricate correspondence.

Only the tooling owner edits these mappings. The browser consumes a published mapping whose source/frame matches the proxy bytes.

## 4. Publication transaction

`publish_outputs` contains completed source item versions only. A batch may include a newly completed asset and a scene depending on it. Dependency versions must already exist or be present in an acyclic, valid batch. Unfinished source items belong in a later publication. Their absence does not prevent the completed batch from being published.

The handler derives project/session/work/agent attribution from the admitted invocation. Model-supplied project IDs are assertions to check, not authority. Unknown work correlation is `attribution_unavailable`.

### Before committing

- Validate the closed schema, source closures and staged immutable artefacts.
- Compute a canonical request hash. Repeated `publication_id` plus the same request returns the original result; a different request returns `idempotency_conflict`.
- Validate each proposed version ID, item kind and expected current-published version.
- For every item, compare `expected_current_version_id` with the stored head. New items require `null`. A mismatch is `publication_head_conflict` for the whole batch.
- Validate explicitly declared preview specifications and allocate stable job identities.

### Commit boundary

One SQLite transaction records the publication, completed item versions, compare-and-swap head updates, preview-job intents and durable public events. It must not contain Blender/FFmpeg/service execution. Artefact files are staged and atomically promoted to immutable storage before their ready records are committed. Unreferenced staging bytes are recoverable leftovers, not published output.

After commit, the owned renderer dispatch loop consumes persisted intents. A crash after database commit but before launch cannot lose the job. A crash after launch but before recording its acknowledgement leaves `outcome_uncertain`; inspect worker identity/receipts before any second launch. Use deterministic job/attempt IDs and exclusive worker receipt/output locations to prevent duplicate writers.

### Head ordering and retries

- A delayed publication cannot replace a newer head by arrival time alone. It must still satisfy its expected-head comparison.
- A harmless retry never changes the current head again and never schedules another render.
- To deliberately publish a result derived from an older version, Copilot makes a new version with explicit ancestry and the **current** expected head. It does not reuse the old publication ID or reset the working tree through the UI.
- Source/version records are immutable. Preview availability, assessment and user selection are separate revisioned records.
- An admitted render may attach new frame/segment artefacts to its existing version. That does not mutate the original source publication payload.

For a new camera/profile preview of an **already published** version, Copilot calls `request_preview` with `PreviewRequest`. This admits a render intent without creating another source version or changing the item's current head. It is the normal completion path for Render this view, including inspection of a historical version. Preview intent IDs are unique across initial publication and later preview requests; the same intent/spec/version is reused, and a conflicting reuse is rejected.

## 5. Render admission and ownership

Each preview intent identifies source closure, scene/shot, camera or validated override, sample/range, rational FPS, dimensions, purpose, engine/device and renderer implementation revision. The renderer uses a fixed installed entry point and structured data. Publication cannot supply an arbitrary shell command.

Before launch, verify input availability, closure digest, free output space and the required execution profile. A missing device/model/feature is an actionable state. Do not silently substitute a different quality profile.

Use one owned writer for a render output directory and one owned encoder for a segment. The job acquires the applicable renderer/tool resource claim before starting. Completed outputs use immutable artefact IDs and paths. A repeated worker receipt is a no-op; conflicting bytes for the same frame/job identity are `frame_conflict`.

Renderer memory overrides implement Copilot's declared camera/profile and are not saved into authored sources. Every generated frame has a receipt with job ID, source frame, actual dimensions/hash and input identity before it is exposed for review.

Worker receipt `output_path` is relative to that job's registered output root. Resolve and decode it under the job capability, verify its hash, then atomically register the immutable artefact and frame/segment record. A worker cannot nominate another job's file. Generic tool `ArtifactRegister.staging_path` instead resolves under the active project's permitted staging area. Neither operation serves the mutable staging file directly.

## 6. Minimum source acceptance cases

The [acceptance pack](acceptance/README.md) requires:

- Asset v1 used by a scene, followed by a v2 geometry **and texture** edit. The old scene reopens/renders from its exact older bytes in a clean materialisation.
- Two dependencies targeting the same path with different hashes fail deterministically.
- A missing LFS object, undeclared texture and missing retained private blob each identify the missing resource.
- Working-tree edits during rendering leave the pinned job's outputs unaffected.
- Batch publication either registers all completed items or none; pending previews are independent.
- Lost publication acknowledgement and crash between commit/worker launch produce one publication and one admitted job.
