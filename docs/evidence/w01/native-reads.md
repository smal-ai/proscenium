# Read-only native metadata wrappers

Added after the bounded child-consumption proof on `impl/w01-child`, based on
`b29d5b4ad027fae929dbd61791cc79a253b6b73a`. **No additional cloud root or native
child was executed.** The previous live proof's source hashes describe that proof,
not an unperformed live test of these new wrappers.

| Native tool | Accepted implementation | Normative result |
| --- | --- | --- |
| get_artifact(artifact_id) | artifacts.get | Artifact |
| get_version(version_id) | catalogue.version | VersionRecord |
| list_job_frames(job_id, cursor?, limit?) | previews.records + F00 Cursors.page | FramePage |

All three bind to the active project and original persisted native invocation/work,
reject invalid IDs and model-supplied scope overrides, and validate closed result
shapes before returning them. Frame ordering, same-input/old-attempt filtering and
cursor project/job scoping match the accepted preview/F00 read path. No new schema,
HTTP endpoint, mutation or dispatch path was introduced. Existing get_job now shares
the same strict Id argument validation.

The default required-model authoring specialist's tool list includes the new reads.
Explicit configured tool lists are not silently widened. Profile/Blender instructions
document `.proscenium/artefacts/sha256/<full sha256>/content` for ready bytes and direct
the agent to verify metadata and inspect by hash, never private SQLite or latest-file
guessing. Publication, receipt, source and artefact identities remain distinct.

## Offline evidence

The new tests use actual accepted Artifacts, Catalogue and PreviewService read
implementations with normative synthetic read-model fixtures. They do not execute
Blender or claim that synthetic receipts are real rendered media.

- Exact Artifact/VersionRecord round-trip, including accepted derived output links.
- Three numerically ordered frames paginated as `[1, 2]`, then `[10]`, not lexical
  filename order. A stale input-digest receipt is excluded. The retained old-attempt
  receipt keeps its original attempt ID.
- Signed F00 cursors interoperate with the existing HTTP scope and reject altered,
  foreign-project and other-job reuse. Limits reject zero, booleans, fractions and
  values above 100.
- Active-project and native session/agent/work attribution is required. Invalid IDs,
  private-path strings and scope overrides are rejected.
- Missing records/capabilities do not produce fabricated records or install services.
- Wrong-project/identity and extra private fields in service results are rejected.
- Records, operations, events and outbox contents are identical immediately before
  and after each wrapper call (native invocation admission precedes the comparison).
  Runtime open counts are unchanged, and reads do not release an execution hold.
- SDK argument schemas inline only the exact existing Id definition. No full schema
  catalogue is duplicated, and no competing wire DTO is added.

| Command | Actual result |
| --- | --- |
| `uv run pytest tests/integration/copilot -q` | **74 passed**, one existing Starlette TestClient deprecation warning, 2.95 s. Includes 24 new metadata-read cases. |
| `uv run mypy apps/backend/src/proscenium/copilot/native.py apps/backend/src/proscenium/copilot/tools.py` | Passed, two source files. |
| `uv run ruff check apps/backend/src/proscenium/copilot/native.py apps/backend/src/proscenium/copilot/tools.py tests/integration/copilot/test_native_reads.py` | Passed. |
| `uv run ruff format --check apps/backend/src/proscenium/copilot/native.py apps/backend/src/proscenium/copilot/tools.py tests/integration/copilot/test_native_reads.py` | Passed, three files. |

The live allocation remains **one root and one child used** in the preceding proof.
Full authoring, live child Stop/restart/input, and transport-loss limits are unchanged.
