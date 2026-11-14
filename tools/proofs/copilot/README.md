# E01 official Copilot compatibility probe

**Bounded proof, not the application adapter.** Exact findings and W01 handover are in
[`docs/evidence/e01/README.md`](../../../docs/evidence/e01/README.md).

## Reproduce without a model request

Run from the E01 checkout. These commands install only into the allocated temporary
runtime. They do not authenticate, install globally or create a persistent service.

```bash
uv venv /tmp/opencode/proscenium-e01-runtime/venv
uv pip sync --python /tmp/opencode/proscenium-e01-runtime/venv/bin/python tools/proofs/copilot/requirements.lock
npm install --prefix /tmp/opencode/proscenium-e01-runtime/cli --save-exact --no-audit --no-fund @github/copilot@1.0.112

/tmp/opencode/proscenium-e01-runtime/venv/bin/python tools/proofs/copilot/probe.py \
  --cli /tmp/opencode/proscenium-e01-runtime/cli/node_modules/@github/copilot-linux-x64/copilot \
  --runtime-dir /tmp/opencode/proscenium-e01-runtime/proof \
  --output tools/proofs/copilot/output/local.json \
  --native-controls --lifecycle

/tmp/opencode/proscenium-e01-runtime/venv/bin/python -m unittest discover -s tests/native/copilot -v
```

- `uv venv` must not be used to clear an existing environment. Reuse an existing
  allocated environment and use `uv pip sync` when rerunning these instructions.
- `--auth-status` reads the existing CLI login without making a model request.
  It does not print the account name, token or full authentication response.
- The version check rejects anything other than SDK `1.0.34` / CLI `1.0.112`.
- Use the **direct native ELF binary**, not `/usr/bin/copilot` or the npm launcher.
  The npm loader spawns a child, which weakens a force-kill ownership boundary.
- The offline fake-wire tests cannot start a real CLI and do not infer native
  behaviour from their successful assertions. Captured-evidence tests inspect the
  committed real captures without repeating their cloud requests.

## Cloud modes require a new explicit allocation

**The original allocation is exhausted: eight deliberately admitted root sends,
including the held Steer. Do not rerun cloud modes under this allocation.**

- `--live`: two root sends, one native child, small generated image and two project
  histories. It records the created session IDs in its output.
- `--advanced`: three sends, one native child, hook context and a deterministic
  completion-race deferred Steer. No fake runtime events are injected.
- `--followup --history-a <owned-id> --history-b <owned-id>`: three sends, two cold
  project-history resumes, a real user-input callback, `pwd`, and unavailable
  required-worker-model rejection. Only histories under this proof's isolated
  data directory are accepted.

These flags are mutually exclusive. Every cloud send durably reserves one slot in
`<runtime-dir>/../cloud-budget.jsonl` **before** sending. Timeouts and lost outcomes
do not refund slots. There is no automatic send retry. Never delete or relocate
that ledger to extend an allocation. A new approved allocation needs a separately
named runtime directory and ledger, not a reset of this one.
An already reserved send label is rejected even when the allocation has slots
remaining, so accidentally repeating a command cannot repeat that model send.

Every owned client is closed in `finally`. `stop()` has a bounded timeout, followed
by the SDK's public `force_stop()` on failure. No unrelated process is terminated.
Logs, native state, generated PNGs and SQLite remain outside the repository.

## SQLite correlation replay

```bash
python3 tools/proofs/copilot/replay_attribution.py \
  --capture docs/evidence/e01/advanced.json \
  --database /tmp/opencode/proscenium-e01-runtime/proof/attribution.sqlite \
  --output tools/proofs/copilot/output/sqlite-replay.json
```

This replays actual captured identities into a disposable proof database twice.
It is **post-capture replay**, not proof of application persistence before callback
reply, crash recovery or the production schema. F00 owns those interfaces.

## Lint

```bash
uv tool run --cache-dir /tmp/opencode/proscenium-e01-runtime/uv-cache --from ruff==0.17.3 ruff check tools/proofs/copilot tests/native/copilot
uv tool run --cache-dir /tmp/opencode/proscenium-e01-runtime/uv-cache --from ruff==0.17.3 ruff format --check tools/proofs/copilot tests/native/copilot
```
