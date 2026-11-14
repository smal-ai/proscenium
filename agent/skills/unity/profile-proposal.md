# Native profile registration proposal

**For W01/integration owner. Not installed by W08.**

| Setting | Proposal |
| --- | --- |
| Profile ID | `unity-specialist` |
| Description | Pinned-source Unity import, minimal controller, scene validation and admitted local builds. |
| Instructions | Load `agent/skills/unity/SKILL.md`, active-project context and the original bounded human task. |
| Model | Parent's configured native SDK specialist model. No provider/model override in a project-writable file. |
| Working directory | Registered active Proscenium root, not a reference game or the Unity installation. |
| Memory | Existing policy: native memory and cross-session retrieval disabled on create/resume. |
| Tool registrations | Host wrappers for `prepare`, `bootstrap_project`, trusted Blender export, `run_unity_job`, `reconcile_unity_job`, `cancel_unity_job`, `checkpoint_unity`, and the accepted publication tool. |
| Execution profile | Owner-configured `unity-local-6000.3.27f1`, or an existing equivalent admitted profile. F00 must recognise this exact ID before any launch. |
| Editor | Configured official Unity executable, supplied out of band. No credential reads or home-directory discovery. |

The model-facing operation accepts source version, Unity project-relative path, scene asset path,
spawn, camera projection and requested operation. The host injects `Services`, authenticated `Caller`,
`JobAdmission`, runtime/profile and timeout. These are not capabilities the model can manufacture.

Register the accepted W02 `SourceService` instance as `services.extensions["sources"]`, or adapt that
one name to the integration container. The callable uses the F00 seam, not W02 implementation internals.
The native wrapper imports `tools.unity.jobs.run_unity_job` from this checkout/package path. W08 does
not alter SDK agent profiles, root packaging, router composition or dependency locks.

For cancellation, the parent registers a `jobs.cancellers["unity"]` adapter and its existing outbox
consumer. After `cancel_requested` is committed, call `cancel_unity_job` with an exact-attempt worker
caller and reconcile its receipt. Sending SIGTERM alone is not terminal proof. On restart, reconcile
all admitted Unity attempts before reopening execution. Keep resource claims for uncertain attempts.

**Acceptance still required:** native SDK delegation, real W02 source/publication integration,
configured profile registration and the headless Input System test correction. W08's native suite
reported `failed`, not complete M11 acceptance.
