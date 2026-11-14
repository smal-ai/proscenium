# M11 final continuation: source checkpoint complete, publication blocked

**Criterion 6 remains partial. No `unity_result` version exists.**

The actual compiled owner UI reconciled and continued the original work on accepted base
`904f733`, using preflighted driver commit `ed3232c`. Both owner operations returned HTTP 202.
One acknowledged SDK continuation used the original request/work/session and `gpt-6.1-sol`.
No new Queue, conversation, child, Editor, Blender, export, renderer or provider work ran.

The real lead completed canonical `checkpoint_sources` operation
`m11-unity-source-checkpoint`, native call `call_0KgFTlXlYrinFSJ3axs84711`, producing source
commit `8d49caaee980d18a140111e81b15a46c71e229ce`. Every receipt file was independently
matched to its retained before hash and actual Git blob. Source/meta/GUID bytes, the unrelated
scene sentinel, old reports, jobs, registered artefacts, earlier versions, contexts, original
native bindings and unanswered historical decision remain unchanged.

## Exact new blocker

After checkpointing, native `rg` requested a **read** of the existing source file
`unity/M11/Assets/Proscenium/tool-install.json`. The remaining general permission classifier
still searches the whole serialised permission request for consequential-action words.
The filename matched `install`. The accepted registered-custom-tool correction worked for
the checkpoint. It does not cover this native read request.

- Decision: `decision-fbd9a5d969c84dd82857ba80d5811d112076572904ba29f6f27a09e384c972ff`.
- Native permission: `f4cb7f12-085b-42b8-9288-19b946111107`.
- Tool call: `call_XcQ3lvQAC0HOmPxz3LBd6jzc`.
- Intention: `Search in file: /home/quintin/.cache/proscenium-development/milestones/m11-20261114-native-a/project/unity/M11/Assets/Proscenium/tool-install.json`.
- State: pending, `response: null`, `resolved_by: null`, `delivery_state: not_sent`.

The browser detected the decision immediately. The exact question/correlation was surfaced
to the coordinator, a private blocked snapshot was taken and the owned stack was closed.
The stack ran for 78.20 seconds, rather than spending the allocation waiting for an answer.
All recorded host/browser/SDK PID/start identities are gone. No decision was approved, declined
or patched. No fabricated native terminal response or completion was added.
The original work row remains `awaiting_user` at revision 8. Its one unresolved `rg` invocation
remains recorded as `running`, although the exact owning SDK process is gone. That historical
wait needs production recovery under coordinator ownership, not a private state edit.

**Proposal to the permission owner:** separate native read/write path data from executable
operation semantics after existing active-project and secret/path guards. An in-project read
of `tool-install.json` is not software installation. Preserve actual shell install/push/upload
confirmations. This handover changes no production permission code.

## Evidence

| File | Meaning |
| --- | --- |
| `manifest.json`, `files.json` | Strict partial result and exported-file hashes. |
| `checkpoint.json` | Real receipt, original native invocation and completion-event identity. |
| `permission-blocker.json` | Exact new read permission, unanswered owner decision and classifier location. |
| `continuation.json`, `owner-*.json` | Original continuation/message/operation IDs and actual owner UI receipts. |
| `runtime-identities.json` | SDK/CLI/model identity and exact copied compiled-frontend hashes. |
| `public-*.json`, `*.png` | Full-HD owner browser preflight, readback and blocked state. |
| `preservation.json` | Exact source hashes, old report hashes, preserved records and checkpoint-only ref change. |
| `validation-separation.json` | Original import attempt remains `not_run`, separate operator attestation remains `passed`. |
| `send-headroom.json`, `cleanup.json` | Read-only memory/disk guard and positive owned-process cleanup identities. |
| `private-snapshots.json` | Private before/blocked/after copy paths and hashes. No credentials or raw private history committed. |

The generated source `unity-result.json` already reflected the later operator PlayMode fixture
before this attempt. It stayed byte-identical. The original generic import result is the separate
immutable attempt result, not that mutable generated source file.

Publication `m11-unity-publication` / version `m11-playable-v1`, catalogue UI listing and
publication replay remain **not run**. Checkpoint replay was not entered after the new blocker.
The observer's SDK dataclass serialisation was corrected and tested offline after shutdown.
The actual receipt above comes from the genuine native completion event and durable intent,
not from reparsing that earlier observer's string representation. No native rerun followed.

Retained control: `/home/quintin/.cache/proscenium-development/milestones/m11-20261114-native-a/final-20261114-a`.
Do not reset its guard or send another continuation without a fresh coordinator allocation.

## Scoped checks

**50 offline checks passed**, including eight new final-evidence/observer checks, 11 historical
M11 evidence checks and 31 existing Unity integration checks. Ruff lint/format, mypy on four
files, documentation validation, browser-driver JavaScript syntax and Git whitespace checks
passed. The existing Starlette/httpx deprecation warning remains unrelated. These checks do
not replace missing publication, catalogue UI acceptance or a new native PlayMode run.

```bash
PYTHONPATH=apps/backend/src:. python tests/native/unity/m11_final_export.py \
  /home/quintin/.cache/proscenium-development/milestones/m11-20261114-native-a/final-20261114-a
PYTHONPATH=apps/backend/src:. python -m pytest tests/acceptance/m11_final_evidence_test.py \
  tests/acceptance/m11_evidence_test.py tests/acceptance/m11_publish_evidence_test.py \
  tests/integration/unity -q
python -m ruff check tests/native/unity/m11_final*.py \
  tests/native/unity/m11_publish_host.py tests/acceptance/m11_final_evidence_test.py
python -m ruff format --check tests/native/unity/m11_final*.py \
  tests/native/unity/m11_publish_host.py tests/acceptance/m11_final_evidence_test.py
MYPYPATH=apps/backend/src:. PYTHONPATH=apps/backend/src:. python -m mypy \
  --explicit-package-bases --follow-imports=silent tests/native/unity/m11_final*.py \
  tests/native/unity/m11_publish_host.py tests/acceptance/m11_final_evidence_test.py
node --check tests/acceptance/m11_final_browser.mjs
python -B docs/validate_spec.py
git diff --check
```
