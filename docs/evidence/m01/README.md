# M01 actual integrated proof

**Recovered real journey: PASS.** Read [implementation and strict criteria](../../implementation/m01.md)
for the failed first publication, corrected baseline and viewport limitations.
Inputs and UI decisions are explicitly scripted test-user fixtures. Model,
Blender authoring, isolated CPU rendering and product persistence are real.

## Inspect the result

| Evidence | Contents |
| --- | --- |
| [Changed version](browser-new-version.png) | Actual compiled production UI, 1920 × 1080, blue cube twice as tall. |
| [Retained original annotation](browser-retained-annotation.png) | Historical `loop-v1-clean`, original image and saved pin, while `loop-v2` remains current. |
| [Pin before main-chat Send](browser-pin-before-send.png) | Actual one-pin editor draft. Main-chat Send, not an explicit Save click, prepared it. |
| [Clean first image](clean-v1.png), [marked first image](marked-v1.png), [new image](clean-v2.png) | Exact registered immutable PNG bytes. |
| [Verification](verification.json) | Independent source/closure/media checks, actual checkpoint receipts and explicit limits. |
| [Public records](public-final.json) | Real requests, contexts, jobs, frames, versions, selections, annotation and artefacts. |
| [Submitted-feedback records](public-submitted-feedback.json) | Admission-time context with both image roles and the saved annotation. |
| [Native delivery](native-delivery-redacted.json) | Allowlisted native event identities, model/tool identities, tool-argument digests and actual user-message attachments. No private reasoning. |
| [Public events](public-events-redacted.json) | Allowlisted production publication, request, tool-independent work/job/media and public conversation records. |
| [Native before](native-before-feedback.json), [native after](native-after-feedback.json) | Read-only owned-bridge object dimensions, stable IDs, location, dependency and file-hash checks. |
| [Original publication retry](publication-replay.json) | Exact persisted invocation and original body association, HTTP replay receipt and unchanged identities. |
| [Restart check](restart-verification.json), [restarted records](public-restarted.json) | Persistent native association, closed gate, no duplicate work and no new SDK process. |
| [Identities](identities.json), [processes](owned-process-identities.json), [workers](workers.json) | Exact source hashes, SDK/CLI/model/browser identities, process start ticks, executable hashes and worker input/terminal journals. |
| [Cleanup](cleanup.json) | All observed owned processes exited, independent Blender preserved. |
| [Manifest](manifest.json) | SHA-256 and length of every exported proof file. |

The retained private root is
`/home/quintin/.cache/proscenium-development/milestones/m01-20261114T132348Z/`.
It contains the disk-backed project, Git/LFS source history, machine/project
databases, native history and worker journals. Private logs, browser profile,
pairing code, cookie, CSRF token and authentication/cache data are **not exported**.
Do not add those stores to Git.

## Check retained bytes without new native/model work

```bash
.venv/bin/pytest -q tests/acceptance/m01_evidence_test.py
.venv/bin/python tests/native/studio/verify.py \
  /home/quintin/.cache/proscenium-development/milestones/m01-20261114T132348Z
```

The second command also verifies retained private source/media/history files.
It starts no Blender, browser or SDK. Re-exporting the allowlisted evidence is
safe and starts no external service. It updates verification timestamps.

A new live run requires a **new explicit bounded allocation** and fresh root.
The drivers are intentionally not part of default pytest discovery. Bootstrap
starts the real CLI/lifespan and isolated Chromium. Pairing is consumed inside
the browser driver, never printed in chat. The `send` action rejects an already
admitted identical prompt and reserves a durable prompt-hash marker before UI
submission. An uncertain marker requires inspection, not another Send.

## Executed checks

- `uv sync --frozen`: passed. No lock changes or global installation.
- `npm run build`: passed using already installed dependency bytes. The reused
  checkout's `package-lock.json` was byte-identical. Existing Lucide directive
  and bundle-size warnings remain.
- Targeted integration regressions: **53 passed**. See implementation criterion 7.
- Ruff check/format, mypy for launcher and exact SDK MCP configuration, Node
  syntax check and Git whitespace checks: passed.
- Retained-evidence tests: results recorded in `checks.json` after execution.

The successful native loop used the initial launcher extension. The later durable
pre-spawn guard received an offline lost-ack test, not another native run. Source
identities distinguish these revisions instead of pretending they are identical.
