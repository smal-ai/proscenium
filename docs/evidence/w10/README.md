# W10 evidence

## Integration follow-up on `9720285`

[`integration-checks.json`](integration-checks.json) records the current follow-up,
not a blanket replacement of historical evidence below.

- Scoped quiescence fix `78cb875`: the reported combined integration suite passes
  **153 tests**. Protected unrelated SSH/OS sessions no longer veto backup, while
  owned journals, corrupt identities, positively scoped writers and native-home
  ambiguity still block. No daemon-name allowlists remain.
- Final committed W10 suite: **48 passed**. Earlier affected applicability/proof unit subset:
  **17 passed**. Ruff, formatting, mypy (79 files), production UI build and full
  specification/generated-binding checks pass.
- [`ssh-product.json`](ssh-product.json): **real** same-host loopback OpenSSH and
  compiled Chromium product traffic. UI pairing, project open/activate, WebSocket
  replay/reconnect, PNG upload, hash and exact range-byte assertions passed.
  The run remains **partial/exit 2**, because Uvicorn's final SIGTERM re-raise
  prevented the original execution-guard receipt from being written. Counters are
  not invented as zero. All owned processes/descendants were cleaned up and the
  protected SSH session retained its PID/start identity.
- `7d5e108` fixes persistence inside lifespan shutdown, with an offline ordering
  regression. No second temporary sshd/browser allocation was started.
- `708d899` supports explicit coordinator applicability of historical tested
  commits without rewriting them or waiving limits. No release-ready receipts were
  fabricated. [`integration-coverage.json`](integration-coverage.json) still refuses
  release and leaves M10 and cumulative requirements explicit.

No fixture keys, pairing data, cookies, browser profile or private logs are in this
directory. They remain only in the private disk allocation. The raw SSH report
contains redacted command hashes, runtime versions and non-secret request metadata.

## Original W10 package evidence

**14 November 2026, SAST. Tested application commit:**
`727cb2ff663e669970b2ef79e6d19227cb392459`.

- [`accepted-checks.json`](accepted-checks.json): final exact commands, source/evidence
  hashes, **35 passed** checks and remaining limits. Twenty-eight W10 cases plus
  seven selected accepted-interface regressions. No broad browser/model rerun.
- [`offline-junit-accepted.xml`](offline-junit-accepted.xml): final pytest results. All pass,
  none skipped. The existing Starlette/httpx deprecation warning remains.
- The final CLI results in `accepted-checks.json` record separately executed
  backup/verify/restore against the final disposable fixture. Fourteen private
  files backed up, verified and restored. A repeated restore refuses with exit 2.
- [`coverage-accepted.json`](coverage-accepted.json): all 31 case maps resolve. C15 and
  C20 have all their mapped offline checks observed in this evidence set. Native,
  browser and cumulative gaps remain explicit. `--release` correctly exits 1.
- [`native-layout.json`](native-layout.json): owned native proof-directory metadata
  and pinned SDK reference. No native conversation contents were read into model
  context or published in this evidence directory.

Initial evidence at `0b384ce` is retained in `checks.json`, `cli-results.json`,
`offline-junit.xml` and `coverage-report.json`. The subsequent failed process-guard
runs are retained as `offline-junit-hardening.xml`, `offline-junit-final.xml` and
`offline-junit-confirmed.xml`. The names reflect their original invocation, not a
passing status. `accepted-checks.json` explains the failures and exact corrections.
No safety guard was disabled and no unrelated process was killed to obtain a pass.
The three raw failed-run XML files are byte-preserved with scoped binary-diff
attributes. Their generated traceback whitespace is part of the hashed evidence.

The coverage report's **unexecuted** state means no compatible execution receipt
was supplied to this invocation. Existing W01–W08/E03 evidence and its limits are
not erased or implicitly converted into full composed acceptance.

Ruff check/format, mypy (71 source files), specification validation and Python
binding parity pass. Broad `make spec-check` remains incomplete because this
isolated worktree lacks the Node `json-schema-to-typescript` dependency. No frontend
dependency installation/build parity is claimed for a Python-only operations change.

No real native history cold read, relocated inference continuation, browser/SSH,
GPU/model/native authoring or representative performance proof was executed here.
M10 remains blocked without its real proof or an explicit owner scope decision.
The parent retains all composed application lifecycle/route integration and
cumulative M01–M14 acceptance work.
