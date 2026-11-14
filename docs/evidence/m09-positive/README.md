# M09 positive real review → owner approval → native handoff

**Positive integrated journey PASS, pending coordinator milestone acceptance.**
[Strict criteria and disclosed limits](../../implementation/m09.md) distinguish this fresh
run from the preserved [earlier failed allocation](../m09/README.md). No product source
fix was required beyond the two accepted fixes already in base `ad47260`.

Maintained positive drivers/tests commit: `b8a68c043c361c53b12ee2ca2328daa944465cdf`.

## Inspect the actual Full-HD journey

| Evidence | Contents |
| --- | --- |
| [Owner ready](browser-owner.png) | Actual Critic request and real stored visual findings, before native work. |
| [Native ready](browser-native.png) | Real native-requested findings with visual/measured labels and correct source IDs. |
| [Amend and reject before Queue](browser-amend-and-reject.png) | One checked/edited instruction, other finding rejected at revision 2. |
| [After approved native handoff](browser-approve.png) | Submitted selected finding and retained rejected finding. |
| [Normal feedback after VLM cleanup](browser-unavailable.png) | Model unavailable, image still displayed, manual owner feedback saved. |
| [Final state](browser-collect.png) | Retained ordinary review state. |

All screenshots are actual compiled production UI at **1920×1080, DPR 1**, Chromium
`159.0.8154.6`, starting with the first interaction. There are no mocked browser responses.

## Receipts and exact boundary proof

- [Verification](verification.json): strict equality checks and actual allocation counts.
- [VLM observations](vlm-observations.json): actual role-specific request schemas,
  reasoning-off setting, image hashes, response IDs, usage and timings.
- [Owner commands](public-owner-commands.json): actual UI mutations and responses,
  without pairing, cookie or CSRF secrets.
- [Owner run](public-owner.json), [native run](public-native.json),
  [approval + exact retry](public-approve.json), [final state](public-collect.json):
  jobs/attempts/worker events/findings/contexts and native request correlation.
- [SDK delivery](native-delivery.json): exactly two actual sends. Second prompt equals
  the owner amendment and carries one approved finding with exact target and revision.
- [Triggering tool ticket](native-tool-ticket.json): exact four-field result plus digest.
  No unapproved recommendation text crossed that boundary.
- [Read-only native tool arguments](native-tool-arguments.json): actual metadata/hash/view/
  Pillow reads of the approved image. No Blender/Unity/source mutation or children.
- [Input fixture](fixture-identities.json), [runtime identities](runtime-identities.json):
  exact source/compiled bundle/native binary/model-image hashes and unchanged failed-run
  manifest digest.
- [Baseline](baseline.json), [loaded model](loaded.json), [model cleanup](model-cleanup.json),
  [host cleanup](host-cleanup.json), [allocation](allocation.json): exact instance ownership,
  verified context/cache/batch flags, bounds and resource release.
- [Manifest](manifest.json): SHA-256 and length of exported files.

## Exact identities

- Application base: `ad47260a922612393896c21ff7186d643037c524`.
- Fresh project: `project-68bb06fe-810a-4097-ba83-246f11d73c54`.
- Fixture source commit: `117047d641df77b6131d9c5ab332cbeea037c2c9`.
- Fixture source SHA-256:
  `e10939f978fda7d331f8561cda716a1e70447a7be997e986a8da851506a1863b`.
- Image: `m09-clean-target`, 640×360, 333890 bytes, displayed SHA-256
  `fab5fe51c19b8a3a1ef87f388679d9520a6b6d5badabcae9d517244359782bcc`.
- Model-input clean RGB-PNG SHA-256:
  `11e0bed6dc6c18f42813c2a1ac528587cb449541cae90842c7d43684065209fa`.
- Native session: `defb073b-9c1f-41b6-8a62-d7b12ad11b58`.
- Review-request root: `7dcd8488-7650-4e0c-bc73-6a8e9f86e05b`, work
  `work-c352f69cc7404fb1bf2cf80b5dffa79a`, native message
  `d65afcbb-95ee-473d-bd76-40eb0c837a03`.
- Tool callback: `call_UpJPjDl3UKz3RgpMOphr3Xeg`. Ticket digest:
  `ff67d370aab458a32e114b9ccd56e7239a5168c5422e021197d65132f02c3830`.
- Approved root: `5ca5f3e1-377a-445c-ae7a-07e30a79a3bd`, work
  `work-207c5ccdd6854246a4d09aa81aff7ec6`, native message
  `21f96e9d-b29f-4722-9ed4-24283dd2ac4b`.
- Approved immutable context: `66fdc701-7873-4c2c-9636-58688bdec306`.
- Approved finding: `m09-positive-native-review-finding-0`, selected revision **1**,
  subsequently stored as submitted revision **2**.
- Declined finding: `m09-positive-native-review-finding-1`, rejected revision **2**.

| Origin | Review | Job / attempt | Result |
| --- | --- | --- | --- |
| Owner | `a163de50-72c8-448d-9744-0664ac9ba9ec` | `review-job-7220976a-6e87-49dd-96cd-4d3ca201ea2c` / `attempt-3140c9e5-dcc2-4da9-af59-604f8dde1d03` | Ready, succeeded, null work and original owner operation. |
| Native | `m09-positive-native-review` | `review-job-006210a8-eda4-4659-bf26-ef399be76192` / `attempt-8ae4d865-9728-4c03-9193-9405185d6fcf` | Ready, succeeded, exact originating native work. |
| Post-cleanup unavailable owner check | `4c8bf631-fe1e-4fd8-849f-2604858df306` | `review-job-c648bb34-5180-490d-8ee2-279453c369b7` / `attempt-210a4ce1-35ec-42d7-8d8a-43a2a906dadb` | Failed metadata preflight, no third inference. |

After cleanup, actual manual owner feedback saved annotation
`5cd17a06-8374-4a5e-9368-278d63ae893b`, revision 1, operation
`ccf99c81-c857-4e8e-8189-7c9d2cce856b`. That annotation was not automatically queued.

## Model, usage and limits

One exact installed-skill load was run, without overrides:

```bash
uv run --script /home/quintin/.config/opencode/skills/lmstudio/scripts/lms-load.py --config /home/quintin/.config/llmster/models.toml google/gemma-4-e4b
```

It exited zero. Instance `google/gemma-4-e4b` used 65536 context, batch 4096, ubatch 2048,
K/V f16 and TTL disabled. Backend PID was `3545071`. Actual flash flag was `auto`, not an
asserted resolved `on` kernel. Protected Qwen PID `2114016` remained resident with its
original 131072 context, TTL and last-used time.

| Actual response | Time SAST | Duration | Prompt / completion / reasoning tokens |
| --- | --- | --- | --- |
| `chatcmpl-3x1g6imkztb6qcr9j6q2gb` | 16:39:50–16:39:55 | 4.5321 s | 252 / 162 / 0. |
| `chatcmpl-1xgnbp5vo2rb2ycant5b8t` | 16:40:18–16:40:23 | 5.5872 s | 417 / 210 / 0. |

Both returned `finish_reason: stop`. Each used one image and at most 512 output tokens.
Pre-POST MemAvailable was 13583052800 and 11930214400 bytes, above the four-GiB floor.
No conditional third VLM call or cloud root was needed. SDK `1.0.34`, CLI `1.0.112`,
root model `gpt-6.1-sol`; exactly two cloud sends and no native children.

The selected image appears twice in the second SDK send under `render` and `concept`
bindings. Both paths/bytes/hashes are identical. This is disclosed role duplication,
not an extra Queue or a different image. No unapproved finding ID/observation appears
in that send. One exact Queue replay returned the original message ID without another send.

Owner actions are scripted test-user fixtures, not a claim of human creative judgement.
The owner UI requests two visual findings because it has no telemetry selector; the
native review supplies actual measured pixel telemetry and proves both evidence roles.
No source/geometry authoring, Comfy call or W07 original-job continuation occurred.

## Retention and checks

Private browser/authentication/native-history/SQLite/log files stay in the fresh home
directory. They are not exported. The old failed project and old evidence manifest were
not modified. The new exporter and retained-evidence tests start no model or native work:

```bash
uv run python tests/native/providers/m09_positive_export.py
uv run pytest -q tests/acceptance/m09_positive_test.py
```

Targeted final results:

- `uv run pytest -q tests/acceptance/m09_positive_test.py`: **9 passed**, one existing
  Starlette/httpx TestClient deprecation warning. No new model/native call.
- Ruff check and formatting on the three new Python files: passed.
- `MYPYPATH=apps/backend/src uv run mypy tests/native/providers/m09_positive_host.py tests/native/providers/m09_positive_export.py tests/acceptance/m09_positive_test.py`: passed, three source files.
- `node --check tests/native/providers/m09_positive_browser.mjs`: passed.
- `npm run build`: TypeScript and actual Vite production build passed. Existing Lucide
  directive and bundle-size warnings remain.
- `uv run python docs/validate_spec.py`: passed, 119 definitions, 53 operations and
  20 semantic oracles. No shared schemas or generated bindings changed.

No broad test matrix, repeated failed-run proof or extra benchmark inference was used.
