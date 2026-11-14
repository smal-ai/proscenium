# Final one-send selective RPC-loss slice

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
