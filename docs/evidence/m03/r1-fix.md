# M02 R1: scoped callback retirement

Priority correction on `impl/m03-runtime`, accepted application base
`4e8b2337e09dc8416fdc833532f20db473c873a7`. M02's real failure is retained in
`432d3eadb9e9dfd220c84d249ca1addda4ebbb67`,
`docs/evidence/m02/parent-proposal.md`, R1.

## Correction

- Every create/resume callback registration has its own project, native session
  and generation identity. There is no global failure code for the next active
  project to consume.
- After verifying A's settlement, the exact registration is retired and removed
  from the active session cache **before** awaiting SDK disconnect.
- Retired, proven-settled shutdown/idle metadata and sessionEnd hook metadata are
  ignored before reaching the active-only reducer. No broad session/assistant/tool
  prefix is ignored. The retained real A shutdown is the regression input.
- Unexpected execution/error events are not reduced into inactive A or active B.
  A redacted machine-private diagnostic retains original project/session/generation,
  event ID/type and digest, not raw arguments or private native content. Failure and
  admission fencing remain with the originating registration/project.
- Retired permission, input and context callbacks cannot approve an operation or
  inject context. A new registration for the same persistent session cannot inherit
  its retired predecessor's failure.
- Owned-client close/failure paths retire callbacks before SDK shutdown. An old
  disconnect/task exception cannot become a failure of whichever project is current.
- **Inactive Store guards remain unchanged.** Directly reducing the captured
  terminal event into inactive A still returns scope_denied. No shared auth, DB,
  jobs, lifecycle, schema, app, UI, native.py or tools.py changes are included.

## Actual checks

`uv run pytest tests/integration/copilot -q`: **87 passed**, one existing Starlette
TestClient warning, 5.21 s. This includes 13 new R1 cases using the real retained
terminal event plus explicitly offline SDK delivery/control doubles.

The causal test completes A, activates fresh B, admits B's original request and
replays the actual A shutdown synchronously from disconnect. It verifies retirement
already happened, A's tables/journal are unchanged, B remains unfenced, B sends its
original request once, and no A event/native identity or new A work appears in B.
Additional cases cover unexpected retired execution, disconnect errors, revoked
callback authority, exact registration generations, and the narrow metadata filter.

`uv run mypy apps/backend/src/proscenium/copilot/service.py`: passed.
Ruff check/format and staged whitespace checks: passed.

## Live allocation and handover

No additional live request was made for R1. The M03 journey has used five of six
send reservations and one of three children. A new two-project live comparison
needs two roots and cannot fit that remaining budget. M02/coordinator owns a newly
authorised comparison. This code fix and offline regression are not labelled as a
successful rerun of M02.

Both M03 proof hosts, browsers, direct runtimes and helpers were stopped/reaped
before this change. Their original identities, pending/uncertain decisions and
transport-fault records remain retained. No request was restarted or resubmitted.
Parent-owned R2 cancellation/lifecycle work is independent and not changed here.
