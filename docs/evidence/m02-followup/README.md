# M02 narrow real follow-up

**Overall: FAIL. R1: NOT PROVEN. R2: PASS.** This does not claim that R1's accepted
fix failed, nor does it turn the original failed M02 run into a clean first try.

Tested base: `8dd18b86526fda843a62fd0b2056611816308fd0`, including accepted R1
`f96e78b` and R2 `c1b2181`. M01 and the original M02 positive criteria 2/3/4/6
are explicitly reused dependencies. No full history, browser matrix, native
inspection, authoring, GPU/provider inference or Unity workflow was rerun.

## What actually ran

- One fresh root:
  `/home/quintin/.cache/proscenium-development/milestones/m02-followup-20261114T152555Z/`.
- One read-only cloud request in A, Python SDK 1.0.34, direct native CLI 1.0.112,
  `gpt-6.1-sol`. A read its own brief and replied with the correct title/sentinel.
- A's actual native `session.shutdown` followed activation of fresh B. The driver
  then failed on B's disappearing Close-projects button before any B Send.
- Recovery attempted a supported **Reconcile only** of A's own settled history,
  not copied history or another model request. Observation timed out after 40 s.
  The retained operation is `outcome_uncertain`; the native PID record did not
  advance from the first process. No retry or fabricated B result followed.
- R2 was completed separately on active A: three explicitly copied F01 files,
  real W02 checkpoint/publication, one actual planned W03 preview, and a gate
  closed under `previews.lock` before dispatch could proceed. The browser clicked
  the real **Request job cancellation** control. Production's independent
  cancellation watcher settled it, without a fixture dispatcher or gate reopen.
- Native exit code is absent, not zero. No frame, render artefact, render launch
  claim, supervisor/native process journal or Blender process was created.
- Every interaction used 1920 × 1080, DPR 1. Short socket/temp path was
  `/home/quintin/.cache/m2f-4T152555`. All owned process attempts ended below 300 s.
- Original M01/M02 source/index/database/media/native history and original failed
  M02 evidence hashes match before/after. No original project was resumed.

The driver bootstrap/import and detached-popover failures are harness failures,
not R1/R2 product regressions. The metadata reattach's uncertain result is retained
without expanding this task into recovery implementation. A fresh, separately
authorised R1 comparison can use the corrected clean driver path and must still
demonstrate B's first original request running without B Resume or another send.

## Evidence

| File | Meaning |
| --- | --- |
| `verification.json` | Explicit partial verdict, resource use and inherited dependencies. |
| `identities.json`, `runtime-process.json` | Exact tested app/build hashes, fix/model/runtime identities and native PID/start/hash. |
| `native-redacted.json` | Actual A native event IDs/types/times/model/tool metadata, including real shutdown. No reasoning/system content. |
| `r1-a.json/.png` | Actual completed A request in the Full-HD compiled browser. |
| `production-records.json` | Actual requests, sends, association, uncertain Resume, job/attempt, immutable source publication, safe public events and cancellation intent. B has no request or native association. |
| `planned.json` | Actual checkpoint/publication receipt and source closure, explicitly scripted fixture work, plus closed-gate identity. |
| `r2-planned-closed.json/.png` | Actual planned job before owner cancellation. |
| `owner-cancel.json` | Actual UI-generated owner HTTP 202 acknowledgement. No cookie/CSRF/header dump. |
| `cancel-observed.json` | Watcher-settled terminal job, closed gate, zero successful post-close gate Resume calls. |
| `r2-cancelled-closed.json/.png` | Actual UI showing terminal `cancelled`. |
| `bootstrap-failure.json`, `interrupted-before-b-send.json`, `r1-reattach-timeout.json` | Preserved process identities/outcomes of interrupted attempts. Private error logs remain outside Git. |
| `processes.json` | Final R2-only process identities, durations, exits, short tmpdir and prior-data preservation. |
| `protected-before.json`, `protected-after.json` | Original proof/private source hashes, not copied stores or credentials. |
| `manifest.json` | Exported evidence byte hashes and lengths. |

Private logs, pairing URLs, cookie, CSRF, browser profile, authentication and raw
native history are not exported. The gate's post-close observation is retained in
the cancellation receipt. No full audit file was emitted by the original host's
outer SIGTERM cleanup; the driver now persists it at each successful call for
future runs. That later instrumentation received static checks, not another live
allocation.

## Read-only re-verification

```bash
.venv/bin/python tests/native/studio/m02_followup_evidence.py \
  /home/quintin/.cache/proscenium-development/milestones/m02-followup-20261114T152555Z
.venv/bin/pytest -q tests/acceptance/m02_followup_evidence_test.py
```

These commands start no application, SDK, browser or native process. The live
runner is allocation-controlled operator tooling, not a default pytest action.
Its conservative claims and original IDs prevent blind request/control retries.
The final result remains FAIL until the missing R1 live case is actually proved.
