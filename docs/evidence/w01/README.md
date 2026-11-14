# W01 production runtime evidence

**Follow-up:** [Actual native child get_job consumption passed](child-consumption.md)
on the first newly allocated root/child, based on `d068ed9`. It uses a real stored
planned job and production service call, not context echoing. The earlier failed
cases below remain historical evidence. Original `checks.json` and source-hash
manifest describe the original W01 acceptance; the follow-up pins its own bytes.

The subsequent [read-only metadata wrapper slice](native-reads.md) adds get_artifact,
get_version and list_job_frames with **offline tests only**. It consumes no additional
cloud allocation and does not change the earlier live proof's recorded source bytes.

**14 November 2026, SAST. Partial live proof, not full product acceptance.**

Foundation base: `2bc3d5c2c0afe38bf43507d499e8e960855ee3ee`. Tests ran against the
uncommitted W01 implementation on `impl/w01` before its final commit. Subsequent
race hardening is covered offline, not falsely labelled another cloud proof.

| Identity | Actual value |
| --- | --- |
| Python | CPython 3.12.13 |
| SDK | github-copilot-sdk 1.0.34 |
| CLI | @github/copilot 1.0.112, Linux x64 glibc |
| Native binary | Worktree `node_modules/@github/copilot-linux-x64/copilot` |
| Binary SHA-256 | `463978cdea4ad6973b615f1ba07b7837634218632480ef06307ea844ee369a69` |
| Model | gpt-6.1-sol for lead and both explicitly required native children |
| Auth | Official existing login, use_logged_in_user=True, no token extraction/forwarding |
| Process boundary | SDK-owned exec-native PID with Linux parent-death signal |

## Allocation and observed results

Six root requests and two native children were reserved/executed across four
disposable projects. The allocation is exhausted. Every root wait was below
120 seconds. No Blender, Unity, GPU, ComfyUI, LM Studio or independent service was
changed. All owned native process identities subsequently disappeared from `/proc`.

| Run / exact test | Observed result |
| --- | --- |
| `W01_NATIVE_ROOT=/tmp/opencode/proscenium-w01-production-01 uv run pytest tests/native/copilot/production_live.py -q -s` | Failed, 41.83 s. Root context recording completed in production SQLite. One child received trusted hook JSON but declined to consume it. The following shell request exposed reused model turn IDs and stayed outcome_uncertain. No resend. |
| `W01_NATIVE_ROOT=/tmp/opencode/proscenium-w01-production-02 uv run pytest tests/native/copilot/production_live.py -q -s` | Failed, 51.59 s. After scoping turn IDs, root and shell requests completed. `pwd` returned this disposable project with exit 0 and sandboxApplied=false. Child hook delivery was real, but there was one root recording and **zero child tool recordings**, failing the strict two-recording assertion. |
| `W01_NATIVE_PROCESS=1 uv run pytest tests/native/copilot/production_process.py -q` | 1 passed, 5.49 s. SIGKILL of the owned backend killed its actual exec-native PID. A separately owned process remained live, then was explicitly cleaned up. No model send. |
| `W01_EXISTING_NATIVE_ROOT=/tmp/opencode/proscenium-w01-production-02 uv run pytest tests/native/copilot/production_controls.py -q -s` | 1 passed, 6.00 s. Existing history read, actual idle Stop pause/Abort/pending-state barrier, graceful owned reaping, quiet start and cold non-continuing read. Zero root sends. An earlier attempt exposed rewritten private system history and failed before Stop; the public-projection replay guard fixed that without resending work. |
| `W01_DECISION_NATIVE_ROOT=/tmp/opencode/proscenium-w01-production-03 uv run pytest tests/native/copilot/production_decision.py -q -s` | Failed on bounded terminal wait, 113.48 s. Despite the timeout, the real permission UUID/tool join and root input UUID/invocation join were persisted before replies. Owner answer `red` and matching native acknowledgement were durable. The root did not finish before the deadline and its owned runtime was closed. It was not repeated. |
| `W01_DECISION_NATIVE_ROOT=/tmp/opencode/proscenium-w01-production-04 W01_DECISION_MINIMAL=1 uv run pytest tests/native/copilot/production_decision.py -q -s` | 1 passed, 15.14 s. One smaller, separately authorised root completed after a real input callback. Owner expected-revision/fingerprint answer and distinct native delivery acknowledgement were persisted. No children or other tools. |

The initial progress update overstated child callback proof. Actual child **events
and prompt-hook context delivery** are proven. Child tool-callback/model consumption
is not. The profile now explicitly explains the native `<system_reminder>` wrapper,
but that instruction change has not received another child run.

## What is and is not live evidence

- Recording callbacks invoke the production Tools.caller join against admitted
  SQLite invocations and commit a safe recording before returning to the SDK.
  They are not transcript-only replay.
- Native root input callbacks are joined through real `user_input.requested`
  request/tool IDs. The host owner fixture answers the actual durable Decision,
  not a model-generated approval. Native `user_input.completed` confirms delivery.
- Run 03 permission audit uses the real native request UUID, not the older
  fingerprint-derived IDs used during the first two development runs.
- Both child transcripts include the exact persisted additionalContext JSON.
  Both children nevertheless reported it unavailable and made no recording call.
- The live Stop is the **already-idle** production path. It is not a live-child
  Stop or deferred-Steer completion-race proof. Those races are offline production
  tests plus the accepted E01 primitive evidence, not invented W01 live results.
- ACK loss, send-before-ACK and delayed ACK/Stop races in the offline suite are
  explicitly synthetic. No claim of a real network/transport ACK loss is made.
- No native/tool/provider job was blindly retried to turn a failed test green.

Private native histories, logs and disposable databases remain under the stated
`/tmp/opencode/proscenium-w01-*` directories, outside Git. Committed JSON is
allowlisted synthetic-user evidence. Provider/account IDs, credentials, headers,
system prompts and private reasoning are excluded.

## Offline and static checks

The final check results are recorded in `checks.json`: **46 W01 tests passed**.
After consuming accepted dependencies through `66f653b`, the combined suite has
230 passed and two stale F00-only-route assertions failing. Explicitly deselecting
those two shared tests gives 230 passed, two deselected. Their exact identities and
required parent updates are recorded, not silently hidden. Before that dependency
fast-forward, the old foundation combined suite passed 132 tests.
Ruff, formatting, mypy (49 source files) and spec-check pass.
`implementation-source.sha256` pins the final tested production Python files.
Draft source hashes were not captured at each earlier cloud invocation, so those
captures do not certify every byte of the final commit. W01-owned tests include
immutable admissions, full held history, FIFO, native turn reuse, early/duplicate/
out-of-order events, uncertain sends, child context binding, owner channel/CAS,
late input cancellation, separate native delivery, quiet restart, missing history,
Stop pause/send/Abort ordering, exact owned withdrawal, foreign-message fencing,
idle quiescence, all Resume modes, image/mask limits, atomic findings rejection,
policy freezing and schema-reference closure.

The native tool schemas retain the exact normative shape and only referenced
definitions. Counts are 3/20/11/4/1/5/5/2/2 for checkpoint/publication/preview/
artefact/generation/review/admission/retry/state respectively, rather than nine
copies of the full 119-definition catalogue.

Outstanding acceptance: full child authoring and live-child Stop/restart/input,
genuine transport-loss recovery, interrupted-tool continuation,
large-output/Autopilot stress, configured Blender MCP, and parent-composed
source/generation/media/browser journeys. The parent owns those acceptance decisions.
