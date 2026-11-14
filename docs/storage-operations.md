# Storage, configuration and operating contract

**Normative V1 defaults.** Limits can be configured explicitly; changing a limit does not alter an already admitted job's identity or budget.

## 1. Store locations and authority

| Store | Default | Authority |
| --- | --- | --- |
| Machine configuration and project registry | `$XDG_CONFIG_HOME/proscenium/config.json` and `$XDG_DATA_HOME/proscenium/registry.sqlite3` | Human configuration, project IDs/locations and one active project. Contains no creative inference history. |
| Project metadata | `<project>/.proscenium/state.sqlite3` | Requests/work, public event projection, publications, reviews, selections, approvals, budgets and jobs. |
| Managed project bytes | `<project>/.proscenium/artefacts/sha256/<digest>/...` | Immutable uploaded, captured, generated and encoded bytes referenced by artefact records. |
| Retained local-only sources | `<project>/.proscenium/retained/` | Explicitly permitted source bytes excluded from Git, with immutable hashes and backup references. |
| Render materialisations and working output | `<project>/.proscenium/snapshots/` and `jobs/` | Reconstructible input trees, worker receipts and temporary output. Active jobs have exclusive ownership. |
| Large tool output | `<project>/.proscenium/tool-output/` | SDK large-tool spills. Not the OS temp directory. |
| Copilot native session storage | Location reported/configured for the pinned runtime | Authoritative native conversation, compaction/planning data and native artefacts. |
| Project source | The chosen Git/Git LFS working tree | Canonical authored files, brief, plans and permitted source/reference assets. |

Use standard XDG fallbacks when the variables are unset. Credentials stay in their existing supported native/provider stores. The application configuration contains secret references, not copies of credentials. `.proscenium/` is ignored by Git and is not a general agent file-browsing surface; expose scoped project artefacts and tool-owned job records through their interfaces.

SQLite uses foreign keys, transactions, WAL and explicit migration revisions. F00 implements a single migration owner and busy/timeout behaviour. Application API records are projections of native events where indicated, not an independent substitute for native session history.

## 2. Identity and relocation

`project.json` contains a stable project ID, repository ID and project-relative directory settings. The registry maps that ID to its current canonical real path. Files and published dependencies use project-relative paths plus immutable content references.

Owner-controlled limits, access roots and tool profiles are authoritative in private application records, not agent-writable project source. The owner settings API uses the settings record's revision. A project snapshot returns those non-secret effective preferences. Authoring changes to `project.json` do not widen an already registered runtime scope or change an admitted generation budget.

Opening the same ID at a second location is `project_location_conflict`; do not create two owners for the same session/database. The user chooses an explicit **move/rebind** or **clone as new project** operation after the current project settles:

- Move/rebind retains IDs, history and source identities, verifies required stores and changes only the registry location.
- Clone creates new project/repository/session identities and records source provenance. It does not impersonate the original project or silently share its live native session.

Move/rebind uses the owner-only registry rebind operation after files have been relocated and all work is settled. V1 cloning is an explicit owner copy/import of source content into a fresh project created through the ordinary create flow. It does not copy the original project identity, runtime database or live native session as a second active instance.

An external Unity root or sibling-project dependency is not admitted as an agent-readable project location. Bring selected content inside the active workspace through an explicit owner operation first.

## 3. Retention

V1 has no automatic destructive garbage collector. These categories are retained until an explicit owner cleanup/export decision:

- Published source references and local-only retained source bytes.
- Every actual image/mask used in a submitted message or saved annotation.
- Selected reference images, selected deliveries and artefacts required to replay saved feedback.
- Public request/publication/approval/selection history and native conversation state.

Completed unselected frames/segments can later be cleaned only through an explicit operation which shows references and preserves any retained target. A regenerable cache label is not permission to delete the sole bytes of a reviewed frame. Source Git history alone is not an archive of generated images or native conversation data.

## 4. Backup and restore

Back up the registry, project SQLite, required artefact/retained stores, Git/LFS objects and native session files with a manifest of identities and hashes. Use SQLite's backup API. Do not copy a live database's main file without its transactional state.

Native session backup requires a settled session or a runtime-supported consistent snapshot. Include linked native event-log archives where history reads require them. Record SDK/runtime versions and tool/profile configuration separately from secrets.

Restore in a disposable directory first: validate hashes, hydrate required LFS content, restore project/session associations and replay catalogue/feedback. Do not resume model work or external jobs automatically. Missing native history, secrets or local-only media must be shown as separate recovery limitations, not replaced with invented data.

F00 defines schema upgrade/backup preconditions. A failed migration preserves the previous database and leaves authoring unavailable until repaired. An incompatible future database version is opened read-only only if that compatibility is explicitly supported.

## 5. Resource admission

Tooling coordinates its own writes and the isolated renderer has its own admission queue. V1 uses a shared advisory execution-host claim for cooperating **Proscenium heavy GPU jobs**, with capacity one by default. This claim does not control unrelated applications or replace Copilot's planning.

The initiating tool acquires the claim before provider submission and releases it when that job is terminal. The claim covers that submitted job only. A loaded LM Studio or ComfyUI process does not hold it. Status reads and a VLM call do not wait behind a render unless that call itself submits a heavy GPU job. A preview GPU worker acquires the same claim for its own job. A separate renderer-writer lock serialises owned Blender renders. CPU rendering can use a configured bounded thread profile. Lightweight planning, browsing and metadata operations continue.

External services remain independently managed. Inspect their actual queue/memory state as well as the advisory claim: another client may have submitted work. Busy/unavailable resources yield `waiting_resource` or an actionable failure, not queue clearing, global model unloading or killing another application's processes. Release a claim only on verified terminal state. An expired timestamp alone cannot prove that a job stopped.

Before starting a job, check writable storage and configured free-space reserve. The initial reserve is 5 GiB or the profile's estimated remaining output requirement, whichever is larger. Unknown estimates must be reported and the configured reserve still applied. On disk-full, stop owned writes, retain completed receipts, report `storage_full` and require explicit recovery. Do not silently delete old results or substitute another output directory.

## 6. Operator configuration

Configuration includes active project location, application port, Copilot model/profile and runtime paths, independent service endpoint/model settings, generation-attempt default, upload limits, resource claims, render profiles and store/backup locations. Snapshot the effective relevant configuration into admitted jobs using non-secret references.

Endpoint identity is part of a generation job. Resume queries the endpoint recorded for that job, not a newly edited default pointing at a different service. Missing capabilities are unavailable, not fabricated zeroes or a silent change of model/device.

The user-level service owns one FastAPI/SDK process. Browser/SSH-tunnel loss does not own that process lifetime. On restart, read-only restoration and receipt inspection are allowed; execution waits for explicit Resume.
