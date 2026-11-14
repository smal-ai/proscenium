# Native workflow tool integration

**Date:** 14 November 2026, SAST. **Accepted base:** `b9079722800613513fa31bf17649f643e0e70411`.
Specification 3.2.0 and public contract 1.0 remain unchanged.

Implemented native tool registration, a required-model Unity specialist, trusted assembly input
resolution, tracked nonblocking Unity admission and catalogue discovery. This package is
**implementation/offline evidence**, not another Unity/Blender/model proof or cumulative M11/M13
acceptance. The previously accepted Unity input proofs were not rerun.

## Parent composition and lifecycle

```python
from proscenium.catalogue import install as install_catalogue
from proscenium.previews import install as install_previews
from proscenium.workflows import install as install_workflows

install_catalogue(services)
previews = install_previews(services)
workflows = install_workflows(services)  # Idempotent. Starts no process or watcher.

# Restart/observation also works with the execution gate closed.
observed_jobs = await workflows.reconcile(active_project_id)
preparation_blockers = await workflows.pending(active_project_id)
# Account for preparation_blockers alongside F00 work/jobs before settling, switching,
# rebinding or reopening the project. Uncertain preparation is not proof of no launch.

# The existing parent receipt loop periodically calls reconcile. It does not dispatch.
# Existing preview lifecycle dispatches admitted assembly through previews.pump/start.

# The parent job.cancel consumer routes this adapter's Unity jobs after F00 has
# committed cancel_requested. This remains allowed while execution is held.
job = await workflows.cancel(active_project_id, unity_job_id)

# Shutdown: close the execution gate and stop parent dispatch/watchers first.
services.gate.close(active_project_id)
await workflows.close()   # Cancel/join preparation, reap own export, drain IO writers.
previews.close()
services.close()          # Release ownership only AFTER awaited workflows.close().
```

Exact exported hooks:

```python
install(services: Services) -> Workflows
await workflows.assembly(original_caller: Caller, native_envelope: dict) -> JobRecord JSON
await workflows.unity(original_caller: Caller, native_envelope: dict) -> JobRecord JSON
await workflows.unity_result(read_caller: Caller, {"job_id": str}) -> UnityResult JSON
await workflows.reconcile(project_id: str) -> list[JobRecord JSON]
await workflows.pending(project_id: str) -> list[private preparation blocker dict]
await workflows.cancel(project_id: str, job_id: str) -> JobRecord JSON
workflows.cancel_job(job: JobRecord JSON) -> JobRecord JSON  # Synchronous dispatcher callback.
await workflows.close() -> None
```

`install` registers `services.extensions["workflows"]` and a real `jobs.cancellers["unity"]`
callback if that kind has no adapter already. Existing cancellation adapters are not overwritten.
The callback rejects jobs absent from this workflow's private job index and rejects a stale attempt.
The parent still owns outbox selection/routing. Render/encode cancellation remains W03-owned.

`pending` is a private lifecycle report, not a new public DTO or HTTP endpoint. It contains operation
ID, proposed job ID, kind and phase for preparation which has not become a settled launched effect.
It matters because source/export preparation precedes the final Unity `JobAdmission`. A stopped
model callback or missing acknowledgement cannot silently make that preparation settled.

`close` drains the dedicated, tracked IO executor and waits for cancelled source/authoring writers.
It also resolves an in-flight export spawn acknowledgement before stopping/reaping its exact
supervisor. It does **not** wait 600 seconds for an admitted Editor or terminate independent Unity
jobs. Those bounded, receipt-producing supervisors intentionally survive a backend shutdown and
are observed by the next owner's reconciler. There is no untracked `asyncio.to_thread` writer.

No lifecycle/composition/router file was edited. Parent composition above is required before live
acceptance. Use the existing source-checkpoint/publication tools after a completed authoring job.
The application still does not parse creative prompts into Blender code or plan cuts automatically.

## Native model-facing tools

`copilot.tools.Tools.definitions()` now registers the three workflow tools and `list_items` in
addition to all existing source/generation/job/read tools. Each invocation resolves its original
persisted native tool record through `Tools.caller`, including agent/session/work/epoch attribution.
New effects retain the native fence, F00 execution gate and W02 admitted-invocation checks. Only a
proved matching existing effect can be read/reconciled after the gate/fence closes. A changed body,
job ID or originating work/invocation conflicts instead of silently adopting new authority.

Results are validated existing `JobRecord`, `UnityResult` or `ItemPage` records and returned through
native `ToolResult`. Errors use the existing safe code/guidance boundary, never exception repr,
private executable configuration, provider bodies, source runtime paths or caller capabilities.
Tool parameter metadata enumerates configured execution-profile IDs, narrowing assembly to the
installed W03 profile. The model does not have to guess a private profile name.

### `assemble_sequence`

```json
{
  "operation_id": "assemble-original-operation",
  "job_id": "assemble-original-job",
  "version": {"item_id": "sequence-item", "version_id": "published-sequence-version"},
  "execution_profile_id": "blender-cpu"
}
```

- The native-only envelope composes existing normative `Id` and `VersionRef` components. It is not
  a competing `JobAdmission` or another public contract.
- The host resolves the exact W02 `VersionRecord`, verifies its item/project/kind, reads the
  published validated `SequenceMap` and its registered `FrameMap` artefacts, then deterministically
  expands only supported identity/trim mappings when no explicit mapping was published.
- It indexes the exact registered source-job receipts once per source job. Every `FrameReceipt`,
  source job and complete map is validated through shared contracts. Missing/ambiguous frames,
  cross-project records or conflicting artefact lineage are rejected, not inferred.
- The host computes the accepted W03 RFC 8785 SHA-256 over
  `{sequence: technical(sequence), frame_map: full_map, input_frames: receipts_sorted_by_artifact_id}`.
  `technical` removes only optional entry `choice` labels. Input artefact IDs are the exact unique
  receipt artefact IDs. No model must supply thousands of receipt hashes.
- The service calls `previews.assembly(original_caller, JobAdmission_JSON, sequence, frame_map)`.
  W03 retains semantic/source/dimension checks and admits/enqueues through its existing protocol.
  Return is the admitted encode job. Parent preview dispatch performs the actual assembly later.
- A lost facade acknowledgement after W03's committed snapshot/origin is reconciled from that
  exact persisted evidence while the gate is closed, without another assembly admission.
- Creative sequence entries and fixed choices remain agent-authored and published first. No
  automatic cut planning, resize, frame interpolation or mutable filesystem-latest lookup was added.

### `run_unity_workflow`

```json
{
  "operation_id": "unity-original-operation",
  "job_id": "unity-original-job",
  "version": {"item_id": "scene-item", "version_id": "published-blender-version"},
  "execution_profile_id": "unity-local",
  "project": "unity/Walkthrough",
  "spawn": [0, 0.92, -2],
  "camera": "perspective",
  "pipeline": "urp",
  "action": "import"
}
```

`scene_path` is an optional Unity `Assets/.../*.unity` path. Camera is `perspective` or
`orthographic`, pipeline is `builtin` or `urp`, and action is `import`, `validate` or `build`.
Fixture-only repeat import and the F05-specific PlayMode oracle are not exposed as generic world
validation. Successful import/structural validation remains `not_run` for PlayMode, not fake success.
Validate/build require the saved W08 import manifest to match the exact source version/closure,
scene, spawn, camera, pipeline and export hash. They cannot silently validate an unrelated old scene
as though it had imported the newly declared Blender version.

The facade:

1. Persists an immutable private workflow intent and original invocation association before any
   source preparation, tool-script installation or external export. Retries inspect that intent
   and known jobs before any new export/Editor launch.
2. Resolves the exact published Blender asset/scene through accepted W02 services. It verifies the
   complete source closure/hydrated hashes and requires the materialised path inside the active
   project. It rehashes the materialisation again after the export completes.
3. Preserves project lockfiles/live Editors and locally edited installed scripts. It prepares only
   W08's owned namespace, or scaffolds a fresh in-project Unity project without overwriting existing
   package/settings files. Existing GUID-preserving Unity Editor APIs remain unchanged.
4. Launches a tracked, fixed export supervisor. It calls the installed W08 Blender script with
   factory startup, disabled auto-execution, four threads and a 180-second native deadline. No shell
   string, script body or executable path comes from model arguments. The supervisor holds the
   configured rendering OS claim, or a project-local export claim if none was configured.
5. Checks the export source digest, format, metre/axis conventions, nonempty geometry and actual
   Blender version. Source ID comes from the pinned item ID. The private W08 recipe binds export
   SHA-256, source closure, spawn, camera, pipeline and scene path. Recipe numbers are normalised
   before hashing so canonical persistence cannot break a `-0.0`/integer-spelling retry.
6. Constructs normative `JobAdmission` with the accepted W08 recipe digest, then calls
   `run_unity_job(..., wait=False)`. F00 admission and exact attempt/resource fencing precede every
   external Editor launch. The returned model value is the real `JobRecord`, not a completed scene.

W08's default `wait=True` is preserved for CLI/test callers. `wait=False` returns after durable
tracked supervisor identity and the running job update, without calling the 600-second wait path.
Completed own supervisors are polled/reaped during reconciliation and close. A real F00 retry
allocates a new attempt first. An explicit still-authorised original workflow call can dispatch
that new attempt from its frozen export/recipe without another Blender export.

Private namespaces `native_workflows` and `native_workflow_jobs` use F00's existing records table,
not a new database/schema. Prepared assembly snapshots, source identity, frozen executable choices
and exact operation/caller binding live there. Export request/process/terminal receipts live under
the active project's `.proscenium/jobs/native-workflows/`. Copied exports are authored beneath
`unity/exports/<operation-hash>/`, not an arbitrary model-selected outside path.

An ambiguous preparation or spawn remains `outcome_uncertain`/pending. Reconciliation may observe a
live exact process or finish a proven export receipt, but **never** launches an export or Editor.
An existing blocked planned Unity job is observed as planned until an explicit valid retry. Changed
project location is fenced rather than reading a stale absolute root. A copied failed/uncertain
intent is not permission to rerun an external effect.
Known preflight failures before the durable authoring/export marker are recorded as failed rather
than leaving a phantom native blocker. No-effect metadata discovery can be retried under the same
original authority. New or corrected declared inputs still need new operation/job identities.

### `get_unity_result`

```json
{"job_id": "unity-original-job"}
```

The host reconciles only this adapter's exact known Unity attempt, then returns its actual normative
`UnityResult` only when terminal and present. Running, uncertain or result-less jobs return a safe
failure directing the model to `get_job`. It never invents pipeline/editor/scene/validation fields.

### `list_items`

```json
{"query": "stone", "limit": 50, "cursor": null}
```

All fields are optional. `query` is at most 512 characters and matches case-insensitive substrings
over item name, description, tags and recorded provenance `source_url`, `creator`, `licence`, `note`.
It uses accepted W02 `catalogue.item_view` and F00 signed pagination, with cursor scope bound to the
active project and case-folded query. Return is normative `ItemPage`. It does not mutate sources,
catalogue rows, events or jobs. There is no vector store or model-visible private database path.

## Required native profile and private configuration

`Native.open` registers both the existing `authoring-specialist` and concrete `unity-specialist` on
create and resume. Unity gets an explicit tool list and the accepted Unity skill instructions.
`model_policy="required"` is set on both. `runtime.worker_model` remains mandatory.
`runtime.unity_worker_model` is optional and otherwise uses that explicitly configured required
worker, not an available-model fallback. Both memory controls, native Autopilot, shell, context
delivery, MCP configuration and the owner's explicit authoring tool list remain unchanged.

`runtime.unity_editor` is an optional absolute private executable setting, required to perform a
Unity operation. No workstation installation path is guessed. Blender comes from the existing
private `rendering.blender` setting. Execution profile IDs must already be configured. The updated
`config.example.json` shows the new setting and read/workflow tool names. Existing explicit owner
`worker_tools` lists are not silently widened. Add the desired read/workflow names when updating
that owner configuration. These SDK specialist names are not new shared ProjectProfile DTOs.

## Offline evidence and limits

Fixtures use actual shared F00 job/resource/state services, W02 catalogue/artefact views and W03
assembly admission, with explicitly synthetic source documents and fake subprocess/SDK transport.
Tests cover fixed export argv, source identity, pre-launch intent/admission, nonblocking return,
closed-gate reconciliation, lost acknowledgement, ambiguous spawn fencing, F00 retry without
re-export, exact cancellation authority, thread/async-spawn shutdown, native fence/owner denial,
assembly digest/result, required profiles and read-only scoped catalogue search.

Commands:

```bash
PYTHONPATH=apps/backend/src:. python -m pytest tests/integration/workflows tests/integration/unity \
  tests/integration/copilot/test_native_reads.py tests/integration/copilot/test_tool_failures.py \
  tests/integration/copilot/test_native_configuration.py tests/integration/copilot/test_child_allocation.py \
  tests/integration/copilot/test_accepted_services.py -q \
  -k 'not test_wire_create_and_resume_both_memory_controls_and_owned_exec_launcher'
MYPYPATH=apps/backend/src:. PYTHONPATH=apps/backend/src:. python -m mypy \
  --explicit-package-bases --follow-imports=silent apps/backend/src/proscenium/workflows \
  apps/backend/src/proscenium/copilot/{tools,native,workflows}.py tools/unity/jobs.py tests/integration/workflows
python -m ruff check apps/backend/src/proscenium/copilot/{tools,native,workflows}.py \
  apps/backend/src/proscenium/workflows tools/unity/jobs.py tests/integration/workflows
python -m ruff format --check apps/backend/src/proscenium/copilot/{tools,native,workflows}.py \
  apps/backend/src/proscenium/workflows tools/unity/jobs.py tests/integration/workflows
python -B docs/validate_spec.py
git diff --check
```

Final scoped result: **91 tests passed, one environment-blocked legacy test deselected**. This
includes **24 new workflow/discovery tests**. Mypy passed on 13 source files, Ruff checks/formatting
passed, specification validation passed and whitespace checks passed. The existing Starlette/httpx
TestClient deprecation warning remains unrelated.

One pre-existing SDK configuration test is environment-blocked in this worktree: its default
application-local `node_modules/@github/copilot-linux-x64/copilot` binary is not installed here.
The original unfiltered run recorded that missing-file failure. The added owned-path fake-transport
create/resume test covers both profiles and unchanged memory/Autopilot/shell settings without
installing or executing a runtime/model. This is not a claim that the blocked test passed.

No Blender, Unity, cloud/model, GPU or browser matrix execution occurred. No shared job/db/auth/
contract/router/lifecycle/renderer/provider/frontend code or dependency lock was changed. Parent
composition and live cumulative M05/M11/M13 acceptance remain separate from this implementation.
