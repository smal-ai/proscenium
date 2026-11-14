# Clean final M02 R1 proof

**R1 PASS. Strict cumulative M02 PASS using accepted dependencies.** R2 was not
rerun. Original M02 criteria 2/3/4/6, atomic publication and M01 are reused exactly
as authorised. The coordinator makes the individual milestone acceptance commit.

Tested base: `e3cddf8e90f49a627c7630dab12669d97f1c3dfd`.
Whole live proof: **26.570809969 seconds**, one actual production server, one
continuous browser connection/context/page, two original cloud requests.

## Actual sequence

1. Paired the isolated browser normally. Project API creation and local brief
   writes were explicit setup fixtures. Both projects existed before activation.
2. Activated A through the saved selector. Waited for the actual header/project ID
   and detached popover, without clicking Close. Sent A through the actual UI.
3. Waited for A's original request and work to complete and its native ID to bind.
4. Selected/activated B through the same page. Waited for B's header and unmounted
   popover. Observed A's actual native shutdown event before submitting B.
5. Sent B's first original request through the UI. It completed once, with its own
   native ID/title/sentinel, no Resume and no unrelated attribution error.
6. Reloaded B's metadata quietly in that same browser connection. Its completed
   request/native ID did not change. Stopped the owned server, SDK and browser.

A's actual shutdown occurred at `2026-11-14T15:55:24.823Z`; B's request POST was
`2026-11-14T15:55:25.310Z`. Browser POST trace, raw-native event IDs and persisted
request/message correlations independently establish this order.

| Evidence | Contents |
| --- | --- |
| `verification.json` | Explicit verdict, two request/session IDs, resource bounds and inherited dependencies. |
| `A.json/.png`, `B.json/.png` | Actual 1920 × 1080 compiled UI, completed requests and distinct project-specific responses. |
| `browser-result.json` | Single continuous connection/context/page and actual POST-path trace. No credentials or headers. |
| `setup.json` | Authenticated API-created fixture project identities; not model-authored content. |
| `native-redacted.json` | Real native session/model/tool/user-message IDs, shutdown events and byte hashes. Both tool calls were read-only `view`; no private reasoning/system messages/results exported. |
| `production-records.json` | Actual request/context/work/invocation/send/session/control records and safe public events. All public native event IDs were checked against A's complete native ID set for B contamination. |
| `quiet-reopen.json` | Unchanged original B request/native ID after browser reload, without another SDK send. |
| `runtime-process.json`, `processes.json` | Exact owned PID/start identities, executable hash, exits, executed-driver hashes and total duration. |
| `identities.json` | Pinned test source, SDK/CLI/model and compiled frontend hashes. |
| `protected-before.json`, `protected-after.json` | Unchanged earlier evidence and selected retained private source/history/media stores. |
| `manifest.json` | SHA-256 and length of exported evidence files. |

The two cloud execution intervals were **7.133 s** and **5.329 s**. SDK 1.0.34,
direct CLI 1.0.112, required lead `gpt-6.1-sol`, Chromium 159.0.8154.6. No child,
render, inspection, source-history fixture, proxy, media fixture, provider/GPU or
Unity job ran. Existing native/model services were untouched.

Retained private root: `/home/quintin/.cache/m2c-20261114T155508Z/`.
Short disk-backed socket/temp root: `/home/quintin/.cache/m2c-20261114T155508Z/t/`.
Pairing code, cookie, CSRF, private logs/authentication, browser profile and raw
native history remain outside Git. Only allowlisted fields are exported.

Expected shutdown fencing is disclosed: after both requests completed and the
browser metadata check passed, normal server shutdown fenced B with
`awaiting_resume`. B has no `attribution_unavailable` state or Resume operation.
Do not confuse deliberate final shutdown with the retired-callback bug.

## Recheck without another model call

```bash
.venv/bin/python tests/native/studio/m02_clean_evidence.py \
  /home/quintin/.cache/m2c-20261114T155508Z
.venv/bin/pytest -q tests/acceptance/m02_clean_evidence_test.py
```

These commands start no application, SDK, browser or native process. The live
runner consumes one explicit allocation and creates an exclusive local allocation
marker before process startup. It has no retry/recovery/reattach path. A failure
retains its effects and stops rather than sending another prompt.

Accepted R2 dependency is the actual proof at commit
`a0c15ba7d95193095718a730aabf6b833b77c496`, path
`docs/evidence/m02-followup/verification.json`. The original failed M02 proof under
`docs/evidence/m02/` remains unchanged, not rewritten as a first-attempt success.
