# Host-native operator guide

**14 November 2026, SAST.** These are implemented commands and operating
preconditions. [Implementation progress](implementation/progress.md) records the
accepted revisions and remaining release checks. Use the maintained Proscenium
checkout and its pinned tools.

## Bootstrap and foreground run

Use the pinned Python/dependency locks and the already configured Node toolchain.
Installation is an operator operation, not a model's routine authoring action.

```bash
uv sync --frozen --python 3.12
npm ci --ignore-scripts
npm run build
uv run python -m proscenium --help
uv run python -m proscenium --config /absolute/private/operator.json --pair
```

Start the private JSON from [`config.example.json`](../config.example.json), then
set the installed Unity executable, native models, MCP configuration and independent
service endpoints. `make run CONFIG=/absolute/private/operator.json ARGS=--pair`
builds the frontend and runs the same backend command.

- Exactly one ASGI worker owns the registry and active project. Do not add
  `--workers`, `--reload` or a second backend instance.
- Backend flags are `--config`, `--origin`, `--frontend-dir` and `--pair`.
  `--origin` and `--frontend-dir` override their JSON settings. Models, data paths
  and rendering profiles are configured in JSON. Recovery has its own CLI below.
- `Settings.load()` reads
  `${XDG_CONFIG_HOME:-$HOME/.config}/proscenium/config.json`. Without that file,
  defaults apply. Data defaults to
  `${XDG_DATA_HOME:-$HOME/.local/share}/proscenium`. An explicit `--config` file
  takes precedence and must exist. JSON strings support `${HOME}`, `${CONFIG_DIR}`
  and `${APP_ROOT}` substitutions. Unknown substitutions are rejected.
- Pair only through the URL printed to your trusted foreground terminal. Its
  fragment is private and expires after 15 minutes. Do not paste it into tickets,
  shell transcripts, evidence or a service unit. Browser pairing stores a private
  owner cookie and CSRF identity. Recovery deliberately does not retain those.
- First open a newly chosen workspace through the Projects UI. Opening/restoring
  an existing project does not author source or submit a model request.

The backend composes catalogue, projects, reviews, Copilot, providers and previews
under one shared lifecycle. Startup restores read models with execution held.

## Same-origin SSH access

From the client workstation, using its existing SSH account/authentication:

```bash
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:8765:127.0.0.1:8765 USER@WORKSTATION
```

Open `http://127.0.0.1:8765` in desktop Chromium, Brave or Edge. Keep the configured
origin identical, including host and port. A different local forwarded port needs
a correspondingly configured exact origin and a fresh owner pairing. Do not expose
the backend on `0.0.0.0`, forward the model services to the browser or disable
Host/Origin checks. API, WebSocket, uploads and range media all use this one origin.
Closing the browser or tunnel is not backend cancellation. Keep pairing private.

W10 exercised a real, temporary unprivileged OpenSSH daemon with isolated fixture
keys and strict fixture host verification. Same-host Chromium loaded the compiled
UI through one LocalForward and passed owner pairing, project open/activate, real
WebSocket replay/reconnect, PNG upload and exact range-byte checks. Wrong Host and
Origin were rejected without changing CORS or boundary policy. This is actual SSH,
not a TCP proxy and not a physical remote-browser or cumulative authoring journey.

The [corrected transport proof](evidence/w10/ssh-followup.md) passed with its final
lifecycle audit and process cleanup recorded. The earlier partial receipt remains
unchanged. Full message and film use over SSH has a separate
[M14 journey record](implementation/m14-ssh.md).

## Configuration and resource bounds

Keep the configuration file owner-readable only. It references credentials through
the existing supported native/provider stores, never by copying tokens into JSON.
Paths below are deliberately operator-chosen, not inherited from a reference project.

| Current setting | Implemented behaviour |
| --- | --- |
| `data_directory`, `origin`, `frontend_directory` | Machine state, exact loopback origin, compiled Vite assets. |
| `generation_attempt_limit` | Default 3 per admitted human task/target. Does not reset existing reservations. |
| `profiles`, `execution_profiles` | Public profile descriptions and admitted execution-profile IDs. A listed profile is not proof of device availability. |
| `runtime` | W01 private keys: `cli_path`, `lead_model`, **explicit `worker_model`**, `unity_worker_model`, `unity_editor`, `worker_tools`, `profile_id`, `skill_directories`, `mcp_servers`, `max_continues`. Unknown keys fail. See [W01](implementation/w01.md). |
| `rendering` | W03 `Rendering` keys: executable paths `blender`, `ffmpeg`, `ffprobe`, pinned `build_hash`, `profile_id`, `threads`, `timeout`, `encode_timeout`, `max_frames`, `max_pixels`, `max_samples`, `claim_path`. |
| `services` | Explicit provider ID, endpoint, capabilities and exact loaded model instance ID where required. No embedded endpoint credentials or query tokens. |
| `provider_options` | `review_provider_id`, `minimum_free_vram_bytes` (default 256 MiB), `vlm_max_tokens` (maximum 512) and `image_profiles` mapping provider IDs to installed versioned image profiles. |
| `free_space_reserve_bytes` | Default 5 GiB. Unknown output estimates still require the reserve. |
| Upload/attachment limits | `upload_max_bytes`: 25 MiB. `upload_max_pixels`: 40,000,000. `attachment_max_count`: 8. `attachment_max_bytes`: 100 MiB. |

- The accepted renderer is **Cycles CPU**, up to four threads, 300-second render
  timeout, 60-second encode timeout, and bounded frames/pixels/samples. It is not a
  proven HIP/GPU profile. A missing executable, build mismatch or unsupported device
  must remain unavailable, not silently select something else.
- Saved stateless Geometry Nodes graphs are supported, including nested groups and
  realised mesh output. Simulation, bake/cache and external import nodes remain
  explicit unsupported dependencies. Unresolved instances require authored mappings.
  [The native proof](implementation/w03.md) records the supported path.
- Copilot create **and** resume disable `memory.enabled` and `enable_session_store`.
  Infinite sessions remain enabled, Autopilot `max_continues` is at least five,
  sandbox remains off, and large output spills to project `.proscenium/tool-output`.
- The application has no tested whole-process RAM limit or universal inference
  memory estimate. Do not call Blender's internal memory counters OS RSS. Measure
  actual workload RSS/VRAM before choosing operational service limits. W10 did not
  allocate GPU/model/Blender/Unity work.
- LM Studio and ComfyUI are **independent services**. Configure their existing
  endpoints. Proscenium never installs, starts, stops, unloads or clears their queues.
  LM Studio requires the exact already-loaded vision instance for visual review.
  Comfy preflight checks actual models/nodes, queue/memory and the shared heavy-GPU
  claim. The 256 MiB floor is not a model-fit guarantee.
- Their unavailability must leave saved catalogue/media/feedback available. New
  inference reports the missing capability. No fallback model or replacement media
  is fabricated. Hunyuan has a [real mesh/import proof](evidence/w09/live-b/README.md).
  Its full generated-asset journey is tracked under [M10](implementation/m10.md).

### Versioned image profiles

The host chooses a profile through private configuration. New requests freeze that
profile, endpoint, model hashes and conditioning graph. Changing configuration
does not reinterpret an admitted job.

| Image profile | Required model and input |
| --- | --- |
| `comfy-image-1` | Original FLUX.2 Klein graph and configured model choices. |
| `comfy-sd-turbo-img2img-1` | Verified official SD-Turbo F32 checkpoint, one exact 512×512 reference image. |
| `comfy-sd-turbo-img2img-fp16-1` | Separately identified local FP16 derivative, one exact 512×512 reference image. |

For an already prepared SD-Turbo FP16 service, the relevant private option is:

```json
{"image_profiles": {"local-comfy": "comfy-sd-turbo-img2img-fp16-1"}}
```

Merge that key into `provider_options`, retaining the other required options.
The derivative's exact filename, hash, conversion provenance and measured preparation
are in [the profile record](evidence/m07-fp16/README.md). Preparation alone does not
prove inference fit. The current execution result is tracked in [M07](implementation/m07.md).
The service must already expose the selected model and graph nodes. The generic
256 MiB preflight floor is not a model-specific RAM/VRAM budget.

`services[].available` and `profiles[].scope_status` are configured capability
metadata. `available: null` displays Unknown. A configured Available label is not
a live health probe. Actual generation/review calls verify the required capabilities.

## Quiet restart, Resume and project switching

1. Reopen the same configured registry. Startup restores read models and keeps
   execution closed. Do not add an automatic Resume call to a service unit.
2. Read saved requests, annotations and job receipts. Held Queue requests retain
   original text/context. An uncertain prompt/worker stays uncertain. A missing
   exit code does not become zero.
3. Use **Resume** deliberately. Reconcile-only reads the original native history.
   Continue-work retains the original work/task and does not resend its Queue text.
   Release-held-queue is separate and requires interrupted work to be settled.
4. Before switching, expose wait or Stop Copilot, then any separately supported job
   cancellation. Confirm native cancellation and terminal job/resource evidence.
   A timestamp or dead PID alone is not settlement. Unsupported provider cancellation
   remains unsupported. Do not kill unrelated applications or clear shared queues.
5. Switch only after settlement. Activation closes the new project's execution gate.
   Held messages do not authorise work in a different project.

## Stores and retention

- Git/Git LFS retain authored source and immutable source versions, **not** submitted
  markup, generated image bytes or native conversation state by themselves.
- `.proscenium/state.sqlite3` retains application records. Registered artefacts,
  selected/submitted target images, masks and local-only retained sources require
  their actual `.proscenium/artefacts` / `.proscenium/retained` bytes.
- `.proscenium/jobs` and `snapshots` contain worker working output/materialisations.
  They are not substitutes for registered retained media. The recovery tool excludes
  these reconstructible/settled-worker caches. Required unregistered job output must
  be promoted through the accepted artefact service before a complete backup.
- Project `tool-output` is copied privately for native spill continuity. Native
  session history is private backup material, never injected into an LLM context.
- No destructive GC ships. Do not prune Git/LFS, delete retained bytes or wipe a
  “cache” containing the only reviewed frame. Backup failure never triggers cleanup.

## Private backup

Preconditions are intentionally strict:

1. Stop/settle Copilot and all owned workers through their normal controls, reconcile
   uncertain outcomes, and stop the relevant Proscenium service yourself. W10 does
   not control service lifecycle. Preserve unsaved unrelated editor work.
2. No native Copilot process may remain ambiguously attributable. The tool acquires
   and holds the **existing F00 registry and project flock locks**, checks recorded
   settlement/resource claims, and inspects same-user `/proc` argv/cwd/open files.
   Known native/preview/Unity PID-and-start journals are inspected under those
   exact roots. An active or uninspectable associated worker, corrupt journal, or
   inspectable process positively using a root blocks backup. No daemon-name
   exceptions are used. A protected SSH/auth/OS process with no association is not
   evidence about this project and does not veto its backup. The pinned SDK's
   `COPILOT_HOME` check remains strict for recognisable native Copilot processes:
   missing/ambiguous or overlapping home still blocks, explicit unrelated home does not.
3. Do not run external source writers, Git maintenance or manual native-history
   edits concurrently. These advisory application locks are not an OS sandbox for
   arbitrary programs. The OS scan cannot prove the absence of arbitrary user
   processes with unreadable metadata. The before/after checks do not replace the
   operator's explicit no-external-writers condition, including manual containers.
4. Choose a **new absolute directory on persistent disk** outside live project and
   machine stores. Ensure enough space for source history, LFS, retained media and
   native history. No compression/encryption or cloud upload is implicit.

```bash
uv run python -m tools.operations.recovery backup \
  --project-id PROJECT_ID \
  --target "$HOME/.cache/proscenium-development/backup-2026-11-14-unique"
uv run python -m tools.operations.recovery verify \
  "$HOME/.cache/proscenium-development/backup-2026-11-14-unique"
```

Use recovery's `--config /absolute/config.json` or `--data-directory /absolute/data`
when inspecting an alternate stopped installation. Both SQLite stores use F00's
`Store.backup()`, including committed WAL content. Nonempty WALs use a private
read-only index under the quiescence locks. Absent or zero-byte WALs use immutable
source reads, preventing SQLite from deleting empty sidecars. Only the current known schema
revision is accepted. Source databases are not migrated by this tool.

The manifest records project/repository/native session IDs, DB revisions, Git HEAD
and **all refs and their reachable commits/objects**, LFS OIDs, per-file relative paths,
SHA-256/byte lengths, SDK version, pinned CLI version/hash, runtime-lock hash and
non-secret public configuration. CLI identity is explicitly **lock-only**, not a new
executed runtime observation. Dangling/reflog-only objects and local index staging
are not backup authorities. An unreachable published commit is refused.

The complete source bundle is self-contained and local. Every reachable LFS pointer
is inspected directly in Git objects and its local bytes hash-checked. No fetch,
push, source checkout, staging change, hook or clean/smudge filter is run. Current
tracked/untracked non-ignored working files are preserved separately. Existing
index bytes and unrelated staging remain unchanged in the original repository.
Restoration creates a new index. It does not claim to reconstruct original staging.

The exact associated session trees are selected from persisted associations/work/
events under **`settings.data_directory/copilot-runtime/home/session-state/<id>`**.
Only those trees are copied, including nested checkpoints/native files/event-log
archives. The multi-project native home, auth stores and cross-session stores are
not copied. [Layout evidence](evidence/w10/native-layout.json) records the metadata
observation. New runtime layouts and external/symlinked archives require a reviewed
adapter change, not recursive copying of the whole native home.

- Known secret filenames in historical Git source fail backup, even if deleted
  from HEAD. Runtime auth/pairing/cookie/capability collections are removed from the
  private registry snapshot. Other projects are removed too.
- `.env`, keys, credential files, symlinks, special files, traversal and external
  repository object stores are refused. No Git configuration, hooks or remote URLs
  are copied. Source/native file contents are not a general secret-scrubbing system.
  A user may already have put a secret in ordinary prose. **Never share this bundle.**
- Directories are `0700`, files `0600`. These are private local bytes, not encryption.
  Use an independently managed encrypted operator-only backup/secret store for
  machine login/provider credentials. Reauthenticate via official login after restore.
- Missing native history, missing retained source and missing registered media have
  distinct limitation codes. Corrupt hashes always refuse backup. `--allow-incomplete`
  can explicitly preserve an incomplete recovery copy. It does not waive corruption
  or invent replacement bytes. Restore needs its own acknowledgement of those limits.
- Repeating backup or restore refuses an existing target. Failures leave a private
  partial directory for inspection and never delete it. No `manifest.json` means an
  unfinished backup, not a successful one. No signed authenticity guarantee is made.

## Restore to a disposable location first

Keep the original service offline. Never run two locations with the same project
and native-session IDs.

```bash
uv run python -m tools.operations.recovery restore \
  "$HOME/.cache/proscenium-development/backup-2026-11-14-unique" \
  --target "$HOME/.cache/proscenium-development/recovery-2026-11-14-unique" \
  --rebind-fresh-registry
```

The explicit flag reconciles the original location to `TARGET/project` **only in
`TARGET/machine/registry.sqlite3`**, records `awaiting_resume`, retains stable IDs
and writes `TARGET/recovery-report.json`. It does not mutate the original registry
or duplicate a live conversation. The generated `TARGET/config.json` selects this
isolated machine directory. Pass that file directly to the backend. Use a fresh
loopback port if another backend is serving unrelated work, configuring the exact
origin explicitly.

```bash
uv run python -m proscenium --config /absolute/recovery/config.json \
  --origin http://127.0.0.1:8876 \
  --frontend-dir "$PWD/apps/frontend/dist" --pair
```

Before any continuation, inspect original request text, catalogue source commits,
selections, saved annotations/targets and actual media. Read native history under
the recorded session ID using the composed **reconcile-only** owner operation.
Do not fabricate a new session when it is missing. [M12's actual restoration](implementation/m12.md)
verified cold history, same-session continuation, relocated current-turn images and
held Queue persistence. The original source directory remained present during that
proof. Absolute paths embedded in opaque older native history are retained unchanged.
Their behaviour on a second machine with every old path absent remains unproved.
Working source executable bits are conservatively private in the
restored copy. Git's committed modes remain in the restored index/history.

Promoting the disposable restoration to the sole active installation is a separate
explicit operator decision. Do not import its registry into a running machine or
use it as a clone. A clone uses fresh project/repository/session identities through
the ordinary create/import flow.

## User service template

[`deployment/proscenium.service.in`](../deployment/proscenium.service.in) describes
one loopback backend with compiled frontend assets. Replace the checkout and private
configuration-file placeholders after reviewing the accepted configuration. It does not
start independent services, print pairing secrets, open the execution gate or
install itself. W10 performed no systemd install/start/stop operation. Installation
and lifecycle changes require the operator's separate explicit instruction.
