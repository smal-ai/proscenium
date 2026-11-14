# M14 bounded SSH message/media and unavailable-service proof

## Allocation and preflight

Accepted product base `904f73383d6e9a3e0a5daa411f3628efd90bc2cf`, contract unchanged.
Only proof drivers, affected acceptance checks and this package's evidence are owned.
Cumulative M12/M14 and release acceptance remain coordinator-owned.

- One actual SDK Queue, GPT-6.1-Sol, at most 180 seconds. Only native `view` is
  exposed, with no custom tools or agents. An exclusive durable send reservation
  refuses a second send across process restart.
- One private fixture-key loopback sshd, at most two sequential fixed-forwarding
  clients, one owned API at a time and one continuous 1920 × 1080 Chromium profile.
  Whole allocated proof is bounded to 600 seconds, including cleanup.
- Four-GiB available RAM is checked once immediately before allocation. No waiting
  for resampling, shared-service lifecycle or native authoring/rendering is allowed.
- Build and dependency installation precede allocation. SDK `1.0.34` and the
  official direct CLI `1.0.112` are hash checked. Existing supported login is reused.
- Standalone imports, exclusive reservation, full-body transport hashing without
  auth retention and lifespan-before-termination audit persistence are checked offline.

## Import boundary

Fresh private run root: `/home/quintin/.cache/proscenium-development/s14a`.
The original completed M13 root `c13c` is read-only. Source Git/LFS is cloned without
hardlinks and original source/media/database/native-history bytes are hash inventoried.
Only selected v1 and its source version, catalogue, verified terminal job/frame history,
registered artefacts and the original explicit v1 selection are imported. No original
request, invocation, authentication, annotation or native session/history is copied.

This is an isolated **import of actual history**, not newly rendered media or a
continuation of M13's conversation. The immutable project/repository IDs are preserved
because actual source maps and delivery manifests bind those IDs. A fresh registry and
new native association are used. A single owned `m14-marker.txt` is the only new model
input file. The accepted original selected silent film is 10 frames, 640 × 360, 24 fps,
64,876 bytes, SHA-256 `ddf01466d4a086bcdae27890e6cbabaa2bf309b62e48bf9353a056d92a60aa8e`.

## Execution contract

```bash
uv sync --frozen --python 3.12
npm ci --ignore-scripts
npm run build
uv run pytest -q tests/acceptance/m14_ssh_offline_test.py tests/acceptance/test_ssh_proof_contract.py
uv run python tests/native/studio/m14_ssh_prepare.py /home/quintin/.cache/proscenium-development/s14a
uv run python -u tests/native/studio/m14_ssh_run.py /home/quintin/.cache/proscenium-development/s14a docs/evidence/m14-ssh/result.json
```

The prepare command ran with zero SDK/SSH/browser allocations. Its source
import materialises the selected historical source through accepted W02 tools and
verifies exact ready media bytes. The live command must run once only. No retry of
the live allocation is implied by these instructions.

The unavailable restart uses documented `Settings.load()` configuration, a genuinely
missing direct native executable and unused closed loopback provider ports. Public
`profiles[].scope_status=unavailable` and `services[].available=false` are **configured
metadata**, not successful health probes or evidence of physically stopping shared
services. The driver separately observes TCP refusal at those private configured ports.

## Result: blocked by proof-harness errors

The single live invocation at driver commit `6fa3fe7` stopped in **0.900 seconds**.
It started the one fixture sshd and briefly spawned the first application process.
No forwarding client, browser, native association or SDK send was started/admitted.
No model-message, video-playback or unavailable-service acceptance is claimed.

Two driver defects caused the failure:

1. `tools.operations.recovery.write_json()` intentionally creates files exclusively.
   Rewriting `owned-processes.json` after spawning the API raised `FileExistsError`.
   The sshd's numeric PID/start receipt exists, but the API's second receipt does not.
   In-memory PID/start-checked cleanup still reaped both owned processes.
2. The public evidence parent directory did not exist. Export after cleanup raised
   `FileNotFoundError`. The full failure report was already safely written to the
   private root as `result-private.json`, SHA-256
   `87b0da954ccacb2101e16fcebcbbacd00f1b7a7719dbdfddd9ee33d100113dc9`.

The [sanitised failure receipt](../evidence/m14-ssh/result.json) preserves the executed
revision, source hashes, actual process allocation, failures and remaining gaps. Its
report is an after-the-fact export, not a missing host lifespan audit disguised as one.
Both lifespan audit files are absent because application readiness was never observed.

The corrected driver writes one immutable receipt per process, records numeric
PID/start facts before further persistence, and preflights the evidence destination
before keys or service allocation. Two new offline regressions exercise all six
possible process receipts and a previously missing evidence parent. **No live retry
was run.** The one-sshd allocation is consumed even though its model-send budget is
unused. A new live allocation requires explicit coordinator authorisation.

## Preserved state and checks

- Available RAM at the single pre-start sample: **39,186,788,352 bytes**.
- Original M13 inventory: **195 files**, all byte-identical after cleanup. The
  inventory includes original source/Git/LFS, database, media and native history.
- Isolated project has zero requests, contexts, work records, invocations, native
  associations and annotations. Imported terminal history is not new execution.
- No owned descendants remain. API exit `-15` and sshd exit `0` were actually observed.
  Only exact owned process groups/descendants were eligible for signalling.
- Production build passed with existing Lucide-directive and bundle-size warnings.
  Node syntax check and affected Python lint/format checks pass.
- Initial helper/accepted SSH regressions: **8 passed**. The correction adds two
  targeted regression cases. The final affected run is **13 passed**: six helper
  checks, four accepted SSH regressions and three retained-failure evidence checks.
  These are offline checks, not replacement live evidence. Its JUnit observation is
  `docs/evidence/m14-ssh/offline-junit.xml`.

Still unproved: active-request tunnel cut/reconnect, exactly-once response/context/event
replay, all ten presented movie frames and boundaries, real frame-note reload/downloads/
range/upload through SSH, and unavailable-service review/feedback after quiet restart.
No production defect was reached, so no production/shared/frontend/schema changes were made.

Final offline verification:

```bash
uv run pytest -q tests/acceptance/m14_ssh_offline_test.py tests/acceptance/m14_ssh_evidence_test.py tests/acceptance/test_ssh_proof_contract.py --basetemp=/home/quintin/.cache/proscenium-development/m14-ssh-offline-final --junitxml=docs/evidence/m14-ssh/offline-junit.xml
uv run ruff check tests/native/studio/m14_ssh*.py tests/acceptance/m14_ssh*
uv run ruff format --check tests/native/studio/m14_ssh*.py tests/acceptance/m14_ssh*
node --check tests/native/studio/m14_ssh_browser.mjs
```

The original run root is retained and refuses reuse. A future authorised live run
must use a fresh private root. No result file or historical receipt should be removed
to make the old command run again.
