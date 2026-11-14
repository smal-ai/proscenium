# M09 actual VLM acceptance evidence

**BLOCKED, not complete.** See [strict disposition and fixes](../../implementation/m09.md).
The two real inference calls exposed reasoning-budget and evidence-source failures. No
valid findings, owner approval or approved-finding native handoff was manufactured.

Maintained fixes/drivers commit: `1794ff4cb91bb04be1d835543ea1ebdcde080e8f`.
The role-bound schema refinement in that commit was not run through an extra live inference.

## Actual records

| File | Meaning |
| --- | --- |
| [Result](result.json) | Explicit failed/not-run criteria and actual resource counts. |
| [Runtime identities](runtime-identities.json) | Exact SDK/binary/provider source identities, including which fix was actually executed. |
| [Fixture identities](fixture-identities.json) | Real W02 publication, source closure, precise captured target and measured pixel-telemetry fixture. |
| [Redacted VLM responses](vlm-responses-redacted.json) | Actual HTTP response IDs, usage, timing, truncation and rejected structured candidate. Private reasoning content removed. |
| [Native tool ticket](native-tool-ticket.json) | Exact actual model-facing ticket-only return and SHA-256. |
| [Native delivery](native-delivery.json) | One actual SDK send, its root/session ID and host-captured image/context. No approved-finding send occurred. |
| [Public owner commands](public-owner-commands.json) | Actual browser requests/responses, without cookies, pairing or CSRF. |
| [Public owner run](public-owner.json), [native run](public-native.json), [final state](public-collect.json) | Review tickets, job/attempt/input identities, worker events, exact targets and zero stored findings. |
| [Unavailable run](public-unavailable.json), [refreshed review](public-refresh.json) | Failed metadata preflight after owned-model cleanup and continued actual image viewing. |
| [Baseline](baseline.json), [loaded inventory](loaded.json), [model cleanup](model-cleanup.json) | Protected Qwen preservation, owned Gemma settings and exact-instance release. |
| [Post-unavailable inventory](post-unavailable-inventory.json) | Gemma remained unloaded after the real unavailable-review request. |
| [Host cleanup](host-cleanup.json), [native settlement](native-settlement.json) | Exact own host/SDK identities, no children/claims and native idle. |
| [Manifest](manifest.json) | SHA-256 and length of exported files. |

## Actual Full-HD views

- [Owner review failed safely](browser-owner.png).
- [Native review failed safely](browser-native.png).
- [Unavailable VLM, image still available](browser-refresh.png).
- [Final retained state](browser-collect.png).

All screenshots are actual compiled production UI at **1920×1080, DPR 1**, Chromium
`159.0.8154.6`. Some pre-refresh views retain fetch errors caused by the deliberate owned
host restart. They are disclosed, not cropped away or passed off as a clean run.

## Exact correlations

- Project: `project-452f9789-c39e-467d-b506-c32e3540e98f`.
- Published fixture: `m09-image-item` / `m09-image-version`.
- Fixture source commit: `2f1f39aef3c715b8c4aa56e9f30e744e8e2a799d`.
- Image: `m09-clean-target`, SHA-256
  `fab5fe51c19b8a3a1ef87f388679d9520a6b6d5badabcae9d517244359782bcc`, 640×360,
  333890 bytes. Exact read-only copy of accepted M01 `clean-v2.png`.
- Model input clean RGB-PNG SHA-256:
  `11e0bed6dc6c18f42813c2a1ac528587cb449541cae90842c7d43684065209fa`.
  The provider records this explicit decode/re-encode hash separately from displayed bytes.
- Native session: `d8dae701-2b93-42dd-ae6b-c5c48c483063`.
- Root request: `c630c6e4-b6e1-4a8a-b714-24e4e60fcfcc`.
- Root work: `work-00275b10ed084e0db2fde6d5222c416e`.
- Native message: `9fd3b4c6-948a-4eec-93f6-12d196dcbb1f`.
- Tool callback: `call_K7HIpYsJwm38DASXgVnAqOVH`.
- Ticket-only tool return SHA-256:
  `dc5d12c4e4e658d679f93d687534545f5de8c174b1c225b9d7b788913e978ade`.

| Origin | Review | Job / attempt | Actual result |
| --- | --- | --- | --- |
| Owner | `f3855562-c98c-468d-8ad8-9d9cbca1a988` | `review-job-af808a76-8735-42c0-81b9-9ef8b47dea6e` / `attempt-599ae18b-891e-4dab-991c-fd002f5c7c7f` | Null work, owner operation equals review. Failed `invalid_request` on truncated empty content. |
| Native | `m09-native-review` | `review-job-8ffa671e-187a-4638-b84f-326d12feafaa` / `attempt-6e9de76b-73e8-42cf-b991-427b8d9f5f76` | Original admitted root work. Failed `scope_denied` on invalid measured citation. |
| Owner unavailable check | `30692803-134d-4372-806c-4fa689c563c0` | `review-job-1891a2df-6c14-4960-b862-f464cfa151f2` / `attempt-5b843495-7d8e-4cfd-b0be-a5b11cb953cc` | Metadata preflight only. Failed `capability_unavailable`; no third inference. |

Actual LM Studio response IDs:

- `chatcmpl-pgtl7cv9zms1tka0habf`, 16:12:42–16:12:53 SAST, 10.7863 seconds,
  259 prompt / 512 completion tokens, 509 reasoning tokens, `finish_reason=length`.
- `chatcmpl-qq50y2o71mj1f2ijgdkths`, 16:15:36–16:15:40 SAST, 4.5630 seconds,
  412 prompt / 192 completion tokens, zero reasoning tokens, `finish_reason=stop`.

Both used the sole owned `google/gemma-4-e4b` instance, context 65536, logical batch 4096,
physical batch 2048 and K/V f16. Gemma backend PID was `3460699`; protected Qwen backend
PID `2114016` was not altered. Actual flash-attention flag for Gemma was `auto`, while the
saved profile requested true. No context/batch/KV/TTL override or profile edit occurred.
The installed `lms --version` reported CLI commit `69d945a`. A separate LM Studio engine
build identifier was not captured and is not inferred from the CLI commit.

## Retention and scope

Private logs, browser profile, SQLite, native history, original reasoning and authentication
remain ignored under the original worktree's `tests/native/providers/m09_runtime/`.
The separate disk-backed project is
`/home/quintin/.cache/proscenium-development/milestones/m09-20261114/project`.
Do not delete those stores or rerun inference to regenerate this evidence.

One model load, two VLM inferences, one cloud root, zero children and one exact owned-model
unload were performed. Protected Qwen and saved TOML remained unchanged. W07's original
Comfy image job remains outside this project and was not resumed.

## Targeted checks

- `uv run pytest -q tests/acceptance/m09_vlm_test.py tests/integration/providers`: **80 passed**,
  once after the concrete fixes. These tests are regression/evidence checks, not a new VLM run.
- Ruff check/format and mypy on the changed Python provider/driver/test files: passed.
- `node --check tests/native/providers/m09_browser.mjs`: passed.
- `npm run build`: TypeScript and actual Vite production build passed. Existing Lucide
  directive and bundle-size warnings remain.
- `uv run python docs/validate_spec.py`: passed, including 119 definitions, 53 operations
  and 20 semantic oracles. No schemas or generated bindings changed.
