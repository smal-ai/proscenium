# Native commands denial and custom-tool permission classification

14 November 2026, SAST. Branch `impl/native-denial-recovery`, base `2f1281e`.

## Native authorisation-denial handover

The accepted M10 diagnostic and candidate `7d21240709737144e63f9b6c6f007207fda8b376`
identify a final **commands** gate denial, not an unknown ordinary Bash result.
CLI 1.0.112's distributed README describes permission as a pre-execution gate.
Its native protocol distinguishes a path grant from a commands grant. Captured
ordering is: Bash wrapper start, path request/approval, commands request/denial.
The commands request describes the exact single parsed command, not output from
a process. Prior path approval alone does not authorise command execution.

Static inspection verified binary SHA-256
`463978cdea4ad6973b615f1ba07b7837634218632480ef06307ea844ee369a69`.
Its embedded `copilot.tgz` SHA-256 is
`c9dcab9dd34dd53663cee004e628a799539155850fcd4db6aa94e2615b0ce157`.
The cached native runtime matches that archive byte-for-byte, SHA-256
`d33793ba833da35398474c2f6d817728122896eb19817cc154a9720cb5ffd7e0`.
No native executable or command was run to obtain these hashes. The native core
is compiled Rust, not inspectable JavaScript source; string inspection alone is
not presented as a control-flow proof. The classification relies on the explicit
native commands-gate contract and exact captured gate sequence, not a generic
interpretation of an arbitrary denial string or missing output files.

Passive recovery requires cold resume, exact departed-process/session proof,
empty native pending metadata, no current/unsettled send, original invocation
binding and work/session identity. It joins raw and typed history by native event
ID, requires one exact Bash start and one parsed command, sequential completed
prior path gates, and a final native commands denial. Later/overlapping stages,
unknown stage records, another tool/session/work, changed command, execution
result/partial observation, live owner or generic missing Bash outcome block it.

Raw history is essential: SDK 1.0.34 typed permission projection drops shellType
and hasParseErrors and changes the payload hash. The existing typed fingerprint
is checked against the unchanged stage, while the lossless raw record supplies
the additional pre-execution gate fields. Normal completion matching is untouched.

The invocation becomes **failed due to native authorisation denial**, with a
system event and original source event IDs. No ToolResult, exit code, successful
return or human decision is invented. `copilot.permission_outcomes` records the
native terminal observation separately. Original stage reply, phase, authority
and callback delivery state are unchanged. Only that exact observed stage is
excluded from future payload claims; fresh native IDs remain independent.

Tests were written from the actual redacted M10 IDs/payloads first. **51 focused
denial/permission/departed-runtime tests pass**, including wrong bindings, later
approval/pending stages, live owner, duplicate observation and generic Bash.
Targeted Ruff and mypy pass. Metadata execution and the distinct custom-tool
classification fix are separate follow-ups, not claimed by this code handover.

## Registered custom-tool operation classification

The actual M11 request from `7855b46` exposed a false install classification in
`checkpoint_sources` path data. Known native Proscenium names now use the existing
normative schemas or native read/workflow argument validators. A match also requires
the original live tool name, native ID, session/work/request/delivery binding,
current owner epoch and a hash of the arguments captured at tool start. Descriptions,
filenames and checkpoint messages cannot act as shell instructions or owner consent.

Approval grants entry to the bounded handler only. Existing source/path, job,
generation-budget and caller guards remain authoritative. Unknown custom tools
require an owner decision, and malformed/spoofed known calls reject. Real shell
install/pip/npm/sudo/Git push/upload approval handling is unchanged.

The original filename regression failed before implementation. **34 focused
custom-tool/permission/accepted-service checks pass**, including the actual M11
argument list and a real checkpoint-handler rejection of excluded `.env` input.
Ruff and mypy pass. This does not execute the original M11 checkpoint or answer
its retained decision.

## Departed, unentered checkpoint wait

Read-only inspection found the retained M11 native permission completion
`d35f1be6-dd38-4b29-9bf4-aea761be9004` denying
`cc62c65b-eb3b-4f3d-93a4-740a0925207f` after shutdown prepared a **host rejection**.
The permission decision remains unanswered/not_sent; it is not an owner decline.

A checkpoint-only passive path requires the exact cold/departed session, empty
pending metadata, no current/unsettled send, original invocation/stage/decision
bindings, validated exact native CheckpointRequest, the denied native gate, current
HEAD matching expected_head, and no checkpoint intent or completed operation.
An owner response, approval, changed HEAD/arguments/project or existing intent
blocks it. The lost wait and uninvoked checkpoint wrapper can then be cancelled
with system evidence. No checkpoint effect, successful return or owner answer is
claimed. The decision's delivery state is preserved. Ordinary native callback
delivery matching remains unchanged and may acknowledge an actually matching
prepared host rejection only when its real native completion is observed.

**54 focused denial/custom-tool/permission-stage checks pass**, with production
mypy and Ruff clean. Metadata-only application to the retained projects follows
separately after before-copies. No publication continuation is authorised here.
