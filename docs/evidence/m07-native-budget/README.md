# C12 actual native parent/child admission proof

**PASS for actual native task inheritance and reserved-allowance admission guard. Parent acceptance pending.**
This is not image generation, consumed-attempt exhaustion or another M07 inference attempt.
The earlier M07 colour-quality failure remains unchanged.

## Actual result

On 14 November 2026 at 20:45 SAST, one authenticated owner Queue entered the real W01 SDK
delivery path. The lead called production `submit_generation` twice. It delegated exactly
one native product child, which received W01's trusted `additionalContext`, admitted the
third job and attempted a fourth with a new job ID and renamed alias for the same actual
storyboard frame. The fourth returned **`generation_budget_exhausted`**.

| Counter | Actual |
| --- | ---: |
| Owner Queues / SDK root sends | 1 / 1 |
| Native product children | 1 |
| Native `submit_generation` calls | 4 |
| Successful admissions / retained planned jobs | 3 / 3 |
| Reserved ledger rows | 3 |
| Fourth job / fourth reservation | 0 / 0 |
| Consumed reservations | 0 |
| Provider HTTP / pump attempts | 0 / 0 |
| Provider executions / new creative outputs | 0 / 0 |
| Resource claims after cleanup | 0 |

The lead admitted jobs 1 and 2. The real child performed both the positive third and negative
fourth calls. The four submitted `target_id` aliases differ, but every `settings.target_slot`
is identical and production resolves one canonical target. One original task allowance of
**3** is shared by parent and child. No owner allowance override or policy extension was used.

## Exact identities

- Base: `dd3abd2a9d3a6d7365e824c089276c1ccc1a64b6`.
- Driver committed **before** the only SDK send: `e5787dc50e6ca3afd053e6cff83e6f69abf7ca0e`.
- Retained-evidence exporter and checks: `0ec1c35`.
- Project: `project-f99b7621-5277-42ca-a491-76229a2e2cf6`.
- Request: `2c3ee8b1-9acb-4a4a-bf85-f11de9cc8336`.
- Native session: `e9b93ca9-ea09-4115-9b6b-a47a9d82db0e`.
- Native message: `8770b1a0-d86b-47f9-bd43-b4390c450bd5`.
- Work: `work-05561bb76eb340dc93ff6affd1e36974`.
- Original generation task: `task-881e7b25a14844ad89705d681898ad06`.
- Context: `75d27cbd-2f3c-413b-87d3-bab92e7c5ad3`.
- Child: `bf8e5b76-5e18-4072-b596-1157827da49d`, `authoring-specialist`.
- Canonical target: `storyboard-3688d96cacef07c5437eb9af10f6c481b60563259aca34667f4edaeeae988a42`.
- Slot: `m07-budget-storyboard` / `m07-budget-plan-v1`, `shot-mug`, `frame-mug`.

| Job | Actual native tool call | Result |
| --- | --- | --- |
| `m07-budget-1` | `call_BbK1oJLa5ajqabq6YY1McsPE` | Lead, planned/reserved |
| `m07-budget-2` | `call_sL9EcSVs3aJ2pS20FCZohje7` | Lead, planned/reserved |
| `m07-budget-3` | `call_JN0pGSgkoSM7fuGb3wOFQxUU` | Child, planned/reserved |
| `m07-budget-4` | `call_f9HkkC5UI4HdOw6OD8oAfD2x` | Child, `generation_budget_exhausted`, no job |

`tool-results.json`, `post-cleanup.json` and `native-events.json` retain actual session,
parent/tool/child identities, production invocation authority, attempts, inputs and ledger rows.
The fixture never manufactured a generation Caller, child callback, job row or reservation.
Its separately labelled publication Caller only registered input documents and published source.

## Model, callbacks and input provenance

- SDK **1.0.34**, pinned direct CLI **1.0.112**, normal existing GitHub CLI authentication.
- Root and child used **`gpt-6.1-sol`**. Production config declares child `model_policy=required`.
  The lead also explicitly supplied the same model in the native `task` arguments, so the
  actual child event truthfully reports **`modelSelectionSource=explicit_override`** and
  `taskModelSource=task_argument`, not `configured_required`.
- The real W01 prompt hook ran after native child binding and returned its unchanged
  `additionalContext`. The child invoked tools under that actual agent's persisted authority
  and original root generation task. The lead also repeated root identities in its child
  prompt. This is not a test of hiding those identities from the child prompt or rejecting
  conflicting context.
- Read-only input is the committed actual initial M07 PNG at
  `docs/evidence/m07-fp16/execution/m07-fp16-initial.png`, SHA-256
  `07673e7a7cea364e56b52bb4bc255722678e0cbf20b1b2c714e819c5ab84806d`.
  It was ingested as `m07-budget-real-png-input` in this disposable project.
- Host-selected profile `comfy-sd-turbo-img2img-fp16-1` uses the accepted graph and derivative
  identity. No weights were opened, modified, converted or loaded. The endpoint record
  remained configured with `available=null`, unprobed.
- W02's real artifact/source publication path supplied `m07-budget-graph`, `m07-budget-prompt`
  and the committed storyboard. Production admission validated/normalised reference PNG bytes
  in memory. No encoder process, media export or generated image was run or claimed.

## Deliberately disabled execution boundary

The disposable application ran in-process through the real ASGI routes and authentication,
with no listener or separate server process. `manage_features=False` prevents the normal
provider/preview/workflow lifecycle from starting. Only production W01 delivery was started.
The existing production `Tools.invoke`, `ProviderService.submit`, canonical-target resolution
and F00 reservation ledger remained the effect path.

Two independent fail-closed guards were installed **before** the Queue:

1. A fixture-local provider `pump` guard rejects and counts any execution attempt.
2. The provider HTTP transport rejects and records **every** HTTP attempt, including queue/busy
   probes. No resource claim is used as a substitute for this guard.

Native hooks restrict the allocation to one root-owned `authoring-specialist` task, no child
delegation, and only the named admission/metadata tools. They do not return permission approvals.
The existing automatic production policy handled four ordinary custom-tool permission callbacks.
No genuine owner decision occurred and none was answered by the fixture. The retained callback
journals include their original `reply_prepared` / `delivery_state=outcome_uncertain` records.
Those journals were not cleared or relabelled to make cleanup look cleaner. The native tool
results, child and root completed, while generation jobs stayed planned.

## Bounds and cleanup

- Complete fixture lifetime: **42.858281 seconds**, below 300 seconds.
- Root send to W01 work completion: **37.115126 seconds**, below 180 seconds.
- Child start to completion: **10.292 seconds**, below 120 seconds. Root and child abort guards
  were armed but not triggered.
- Initial MemAvailable: **39177179136 bytes**. Minimum sampled: **37589626880 bytes**, above
  the fixed 4-GiB headroom floor. No GPU work or model-service processes were started.
- Owned host PID/start: `14943` / `47463344`. Owned SDK PID/start: `15068` / `47463469`.
  `cleanup-observation.json` confirms both PIDs absent after the host completed.
- F00 gate closed and W01 fenced at shutdown. No global resource claims remain.
- **All three planned jobs and reserved rows are intentionally preserved** in the disposable
  database. No ledger reset, cancellation, consumed-state injection or manual completion.
- Prior M07 PNG unchanged. No old M07/Klein database, model weights, independent services,
  Blender/Unity sessions, UI, production/shared/schema or dependency files were changed.

## C12 composition and remaining limits

This run proves actual native parent/child inheritance, third admission from child authority,
and rejection of a fourth reserved admission despite fresh job ID/alias. It does **not** prove
three real executions, three consumed attempts, native uncertainty/download semantics or replay
idempotency under original authority. No admission replay was attempted and no extra SDK send
was spent.

Compose it with accepted offline consumed/reserved/uncertain/download ledger checks and the
[actual M07 provider-response recovery evidence](../m07-fp16/execution/README.md), where a real
uncertain reservation remained held through history reconciliation and then became consumed.
Do not describe this admission-only proof as another generation success or as repair of the
recorded red-not-blue quality failure. Final C12 acceptance remains with the parent.

## Checks and retention

**18 focused checks passed:** four new offline preflights, four new retained-evidence checks,
the existing W01 child-context check, three selected offline ledger/alias checks and six retained
actual M07 execution checks. Ruff format/check and mypy on all four new proof files passed.
The existing Starlette/httpx deprecation warning remains unrelated.

`manifest.json` hashes the exported evidence. Exported snapshots omit bulk `copilot.native`
token bookkeeping and duplicate `copilot.bindings`, preserving original per-namespace counts.
Native events are allowlisted to identity, tool, permission, usage and completion events, not
token streams or model-internal snapshots. Full untouched private receipts/database remain at
`/home/quintin/.cache/proscenium-development/milestones/m07-native-budget-20261114/`.
Credentials, pairing/CSRF data, runtime stores and authentication are not committed.

```bash
uv run python tests/native/providers/m07_budget_export.py
PYTHONPATH=.:tests uv run pytest -q tests/native/providers/m07_budget_preflight_test.py tests/acceptance/m07_budget_evidence_test.py
```

The exporter and evidence tests perform no provider or native model calls. The execution driver
refuses an existing run directory, so accidental rerunning cannot create another root.
