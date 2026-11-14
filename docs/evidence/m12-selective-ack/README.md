# Final one-send selective RPC-loss slice

## Final allocated run on `03dbad1`: bounded admission recovery PASS

- [`final-verified-result.json`](final-verified-result.json): authoritative merged
  proof, actual durable checkpoints/timestamps, one withheld response, accepted
  native identity, actual FileIO close/EOF, quiet restart and original admission
  reconciliation without a second send. Final work remains `awaiting_resume`.
- [`final-checks.json`](final-checks.json): exact tested commits, commands, results
  and hashes. No whole M12/M14 completion claim.
- [`final-verified-offline-junit.xml`](final-verified-offline-junit.xml): six passing
  focused preflight/retained-evidence checks, not another native execution.

The SDK send await was pending when native acceptance was checkpointed and the
real descriptor cut. Identity had already been projected from a forwarded user
event before cut; no claim that the ACK was the only possible identity source.
One response was withheld, all genuine hook traffic continued unmodified.

`final-result.json` is the initial export, before cleanup producer receipts were
merged. `final-offline-junit.xml` retains two metadata-consumer failures (checkpoint
08 has raw facts before the verdict, and SDK producer PIDs must join the Node/CDP
inventory). The corrected export reads existing receipts and observes PID absence,
without another SDK send. Both are retained, not overwritten or called live failures.

All 10 target kernel identities are gone, two adopted helpers reaped, no children
remain. No private native content/auth or previous attempt was modified/exported.

## Prior `2f1281e` allocation below: failed explicit-cut/restart proof

**Failed explicit cut/restart proof. No rerun or second send.**

- [`result.json`](result.json): allowlisted actual native acceptance, original
  request identity, missing SDK ACK return, cut-helper error, uncertain outcome,
  PID/start cleanup, runtime hashes and exact source identities.
- `offline-junit.xml`: deterministic ACK-before-hook framing tests for buffered
  and unbuffered real pipes, unrelated callback/event/RPC forwarding, real EOF,
  classification and retained-evidence/privacy assertions. Not another native run.
- `checks.json`: exact commands/results/hashes and consumed allocation.

Native acceptance happened once: user message
`a631f9c9-b43a-4082-aa2c-0ff6dd1e029f` matches request
`14cc9daf-79a8-4907-a164-fe4ac52e70ce`. The SDK send ACK did not return. But the
explicit close helper dereferenced `FileIO.raw`, which does not exist on the real
unbuffered SDK pipe. No targeted close/restart/reconcile success is claimed.

The later helper correction is offline-tested only. The missing in-memory ACK
RPC ID/hash and pre-cut receipt are not invented. All known processes are gone,
all owned children reaped. Old uncertain requests and prior M12 evidence remain
untouched. No full M12/M14 acceptance follows.
