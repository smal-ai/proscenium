# W10 evidence

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
