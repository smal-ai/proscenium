# M03 corrected Resume UI over retained original state

**Result: UI target/revision defects closed at command admission. Actual A
continuation remains BLOCKED.** Both real controls returned HTTP **202 Accepted**.
Neither caused an SDK send. Do not mark the milestone or native continuation passed.

Executed on 14 November 2026 against `e3cddf8`, consuming UI fix `5333468`, the
accepted permission/proxy correction and existing M03 evidence. Actual compiled UI,
production lifecycle/backend, official SDK 1.0.34 and pinned CLI 1.0.112.
Chromium 159.0.8154.6 was **1920 × 1080 CSS pixels, DPR 1 from the first interaction**.
No browser route interception or fake callback was used for the retained controls.

## Preservation and scope

The one named private follow-up root is
`/home/quintin/.cache/proscenium-development/m03-resume-proof-e3cddf8`.
Before opening either application, full private copies of the original A/B roots
were made under `before/a` and `before/b`. All **875 / 583** inventoried files or
symlinks matched the before manifests and original roots. They were verified again
after the controls. Snapshots and private manifests remain outside Git.

The original project/native histories were then operated in place, avoiding any
project rebind or modified native correlation. Every original top-level export,
receipt, image and log remains byte-identical. No original `database-export.json`,
SDK send log, screenshot or native-event export was overwritten. Helper source and
output bytes are also unchanged. New observations and exports live separately.
`run.json` retains before-manifest hashes and per-file original evidence hashes.

No original M10 project was accessed. No Blender, Unity, GPU, provider service,
encoder, new Queue, development child agent or product source change occurred.

## Actual controls and outcomes

### A: explicit original-work selector and Continue

- The corrected UI offered original stopped A and original awaiting-Resume B from
  durable work records. Explicitly selecting
  `work-6361ef13fbeb4f4884ff382bf96d8815` retained A's **work revision 4**,
  not the **project revision 5**.
- Original B was still awaiting Resume with its unresolved `ask_user` invocation.
  One owner UI Stop targeted exactly
  `work-6c566aaa39084a139da0b42fcff0b555`.
  It returned **400 invalid_state_transition before admission/native control**.
  The pending decision, response `null` and delivery `not_sent` were preserved.
  No cancellation barrier or owner answer is claimed. The initial driver waited
  for a nonexistent admitted Stop until its observation deadline; that failure
  record is retained, and the final driver now fails immediately on non-2xx.
- Continue operation **`e7dcff21-3afe-4843-b215-e489ae99fd8f`** sent the correct A
  work ID/revision and returned **202 Accepted**, not null-target/409.
- Reconciliation loaded the original native session
  `83e983b6-bd8f-4485-b483-9e6ba0bbea25`. The Resume operation then became
  **outcome_uncertain / attribution_unavailable** without a continuation send.
  Original A remains stopped at revision 4. The retained B `ask_user` invocation
  remains running and its work awaiting Resume.
- The exact A reconciliation return object was not instrumented in that host.
  Its native guard result and sole retained live invocation are recorded, not an
  invented empty-queue observation. No private bindings, decisions or invocations
  were edited to force progress. No replacement Queue or second Continue was tried.

### B: work-targeted known-ACK Reconcile

- Operation **`08c8cb04-28d8-4d44-94a1-a6f60cae3b24`** targeted original work
  `work-ea4ab25e133747dba8e1f74dad160d2a`, with its current **work revision 3**,
  and returned **202 Accepted**, not revision_conflict.
- Startup advanced that work from revision 2 to 3 through normal restore. Its
  current revision happened to equal project revision 3. This live case therefore
  establishes accepted work-targeted admission, not independently different
  numbers. The targeted controlled-API browser test separately verifies revision
  selection using work revision 23 distinct from the project revision.
- Actual native metadata returned `pending_empty: true`, no uncertain requests,
  stops, continuations or duplicate correlations, but **one live invocation**.
  The unfinished Bash outcome remains unproven. Resume correctly remains
  **outcome_uncertain / attribution_unavailable** and the work awaiting Resume.
- Native history reconfirmed original request
  `82fe4ffb-afc3-4a4f-8745-aad07c7775d2` and known native message
  `2fa74a64-fda2-49c9-abef-710a8cffb705`. The request receipt moved from uncertain
  to acknowledged, which proves admission, **not tool success or exit**.
  No original request was resent. This is not a new genuine missing-ACK proof.

Neither project contained held requests at this follow-up's admission. The held
sets remained empty and neither control selected release-held-queue. Original
held-B preservation through the earlier active-child Stop remains inherited proof,
not a fabricated new held-message case.

## Concrete proposal, not a product edit

`State.submit` in `copilot/state.py` admits the Stop branch, cancels decisions and
then requests `awaiting_resume -> stop_requested`. The normative transition list
in `docs/contracts/state-transitions.json` does not permit that edge, so SQLite
rolls back the entire admission. The UI currently exposes Stop for that exact state.

Parent/F00 should decide the explicit recovery-Stop contract for awaiting-Resume
work, then align its transition and admission. Any implementation must still run
the exact work's genuine native pause/Abort/pending/invocation barrier before
claiming stopped. Adding an edge alone does not establish that the lost `ask_user`
callback will settle, and clearing the live invocation is not a permitted fix.
This proposal changes no public semantics or product file in this package.

## Allocation, checks and cleanup

- Original **five SDK sends** remain retained. Follow-up: **zero SDK sends, zero
  native children, zero helpers** of the authorised maxima one/one. The allocation
  was not spent on a replacement request after a guard refusal.
- Frontier lead `gpt-6.1-sol`, original required worker `gpt-6-luna`, memory and
  cross-session store disabled. No model catalogue rediscovery or full Stop matrix.
- **Four targeted real-Chromium/compiled-UI tests passed** with controlled HTTP/
  socket APIs: durable stopped target, work revision, multiple-target selection,
  exact pending Resume retry after reload. Those tests are not new native proof.
- The pending-original Resume retry live fault was not re-injected. Its producer
  regression passes; it is not mislabelled as observed native recovery here.
- **Fourteen retained-evidence checks passed**, including the seven original M03
  checks and seven new follow-up checks. Ruff passes for the new Python drivers/tests.
- All owned application servers, browsers, native processes, UI-test server and
  UI-test browsers have recorded PID/start identities and are closed. No helper
  started. Exact own-group/root checks found no remaining owned process.
- One initial host bootstrap failed before creating Services because the direct
  Python script lacked the repository in `PYTHONPATH`. Its log/PID record was
  preserved, the harness environment was corrected and the same named run reused.
  No product/native operation occurred during that bootstrap failure.

Files: `run.json`, follow-up-only `public-events-{a,b}.json`, five actual Full-HD
PNGs and `manifest.json`. Private state, native histories and authentication remain
outside Git. Use only the evidence checks without a fresh native allocation:

```bash
uv run --offline pytest tests/acceptance/m03_evidence_test.py tests/acceptance/m03_resume_evidence_test.py -q
```
