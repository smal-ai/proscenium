# Offline M10 permission/proxy correction

Date: 14 November 2026, SAST. Branch `impl/w01-permission-proxy`, base `8dd18b8`.
Inputs: accepted `9f80811` diagnostics in `docs/evidence/m10/`.
This is deterministic regression evidence, **not M10 journey acceptance**.

## Context association

Selected-object validation now reads `Catalogue.version_view(tx, version)` using
the existing validation transaction. Raw publication artefacts, late version links
and exact-version render outputs use W02's existing projection. No immutable version
is rewritten. Existing project/version/source digest, artefact integrity, camera,
spatial sample and exact object/instance validation remains in place.

The captured W03 mapping passes through either its raw association or a late link.
Negative regressions reject a different context project, source digest, version or
instance. Both successful paths leave the stored publication and revision unchanged.

## Permission protocol

- Primary record: `copilot.permissions[key(native_session_id, native_request_id)]`.
  Its original tool/request/work attribution and canonical payload fingerprint must
  agree on every repeated native request event. `State.bind` remains immutable.
- The ID-less SDK callback searches only its original session/tool and exact payload
  fingerprint. Exactly one unresolved stage must exist, it must be pending and its
  complete binding must match that tool's original running/awaiting-user work.
- SQLite commits the callback claim before policy evaluation or waiting for the
  owner. Reply preparation, result and authority are committed before SDK return.
  Returning a reply is not a delivery acknowledgement.
- Native completion must match the stage's request/work and prepared approval or
  rejection. Only then is delivery acknowledged. Completion observations, claim
  identity/timestamp, reply timestamp and earlier stages remain retained.
- A later identical stage is independently claimable only after the previous
  matching stage completed. No native request ID inherits another ID's approval.
  Two pending matches, an in-flight duplicate, a missing match, another session/tool/
  work or an unresolved preceding stage are rejected and fenced.
- Stop cancels unclaimed/claimed stages. A live waiting callback returns a recorded
  host rejection, not owner consent. Restart marks unsettled stages uncertain and
  leaves admission fenced. Legacy records/aliases are not rewritten or reused to
  infer a new claim. This patch does not recover the original failed M10 callback.
- `automatic_policy`, `owner` and `host` remain distinct authorities. The native
  string `denied-interactively-by-user` cannot manufacture an owner decision.
  Existing owner answer revisions, channel checks and delivery records are retained.

The SDK omits a callback request ID. Correlation therefore relies on its preceding
native event and the sole pending stage, not a callback identity supplied by the
transport. This cannot identify an arbitrarily delayed duplicate that arrives in
place of a later stage's otherwise identical callback. It never replays a previous
approval and does not claim stronger transport identity than the SDK supplies.

## Verification

```text
uv run --offline pytest tests/integration/copilot tests/acceptance/m10_defect_repro_test.py -q --tb=short
125 passed, 1 existing Starlette TestClient deprecation warning

uv run --offline ruff check <three owned production files and two regression files>
All checks passed!

uv run --offline mypy <three owned production files>
Success: no issues found in 3 source files
```

The regressions use disposable SQLite projects and the fake SDK adapter. The
configuration test mocks its client and only hashes the pinned installed CLI file.
Its dependency was copied locally from the previous worktree without execution.
Initial configuration failures were missing dependency/path setup, not hidden skips.

No native/model calls, child agents, Blender, Unity, GPU or cloud scenarios ran.
No schema, physical database, lifecycle, job, UI, tool or native profile changed.
The retained original M10 project was neither opened for execution nor modified.
Its request remains outcome_uncertain and work awaiting_resume. Parent owns code
acceptance, Resume UI correction and any explicitly allocated original continuation.
