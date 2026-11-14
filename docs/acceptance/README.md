# Acceptance pack

**Specification:** 3.1.0. **Contract:** 1.0.0, amended before implementation. This pack defines fixtures and expected outcomes. It does not claim that application tests have run or passed.

## Fixture families

F00 implements deterministic generators/readers for these definitions in disposable storage. Generated native binaries stay in test output; proprietary film/game assets are not test prerequisites.

| ID | Fixture | Required oracle |
| --- | --- | --- |
| F01 | [Source fixture recipe](source-fixture.json): floor, cubes, linked asset/texture versions, instance and renamed object | Stable identities, known bounds, exact source/dependency hashes and excluded `.env`/outside-path checks on Proscenium routes. |
| F02 | Perspective and orthographic cameras, non-default aspect/shift, known landmarks | [Coordinate vectors](coordinate-vectors.json), matrix round trip and rendered landmark correspondence. |
| F03 | Twelve numbered frames at 24/1 FPS in segments 1–4, 5–8, 9–12; withhold the middle interval | Sequence never skips 5–8, resumes in order, and preserves a paused annotation when availability changes. |
| F04 | The same numbered sequence at 24000/1001 FPS, including letterboxing/DPR fixtures | Exact rational source↔media mapping and displayed-frame identity at segment boundaries. |
| F05 | Minimal Unity floor/wall/spawn and controller imported from F01-derived assets | Actual Play mode movement/look, floor/wall contact, safe spawn and unchanged GUIDs on repeat import. |
| F06 | [Normalised adapter traces](adapter-traces.json): early events, lost ACK, steering, Stop and late children | [Scenario catalogue](scenarios.json). No model calls or real service changes; E01 adds actual vendor captures. |
| F07 | Fake ComfyUI/VLM providers with mutable status/history and controlled acceptance faults | [Provider trace](adapter-traces.json), prompt/job reconciliation, budget reservations, owner-only findings and endpoint consistency. |

Native generation of F01/F05 belongs to implementation proofs. Do not create a text file with a `.blend` suffix and call it a native fixture. Metadata examples under [contracts/examples](../contracts/examples/index.json) are explicitly synthetic schema cases.

## Numerical and behavioural criteria

- Contract identity, hashes, request counts, frame ordinals, state transitions and GUID retention are exact checks.
- Coordinate-conversion round trips must differ by at most `1e-6` metres for fixture points. Camera projection/annotation placement must be within `0.5` image pixel for the controlled fixtures at DPR 1 and 2. Native renderer landmark comparisons use a documented edge/centroid oracle, not a model's visual opinion.
- The numbered-media tests must observe every expected source frame once during complete playback of the controlled small fixture, with zero blank presentations and zero skipped/duplicated segment-boundary frame IDs. Record the presented-frame callbacks/captures; a timer reaching the clip duration is not evidence.
- Video frame stepping and saved annotation replay must select the intended ordinal at both 24/1 and 24000/1001. Composited/retimed maps have explicit source contributors and cannot be verified by rounded timecode alone.
- F05 player movement must exceed 0.25 m after controlled input, settle within 0.05 m of expected floor support, stay outside a wall collider within a 0.02 m tolerance, and return to spawn within 0.01 m. Fixtures fix controller radius/height and test both supported camera cases. This is not a generic proof for all imported game geometry.
- Representative performance records include source/profile, scene/proxy size, first-ready frame/segment latency, frame-time median/p95, throughput and resource observations. F00/E03 set regression budgets from measured baselines. No universal generation-duration claim is implied.

## Browser matrix

Run the 1920×1080 CSS viewport at DPR 1 with a pinned automated Chromium version. Run installed Brave and Edge against the same numbered media, upload, annotation, decision, Stop and reconnect cases. Record browser/OS/graphics identities. Repeat the coordinate subset at DPR 2 and a letterboxed media rectangle. Mobile acceptance is not required.

For H.264, verify actual decode and boundary behaviour in the installed build. An unavailable codec is a failed compatibility proof, not a passing skipped player test.

## Offline versus live evidence

| Layer | Rules |
| --- | --- |
| Schema/documentation checks | No services, models or native authoring apps required. |
| Unit/contracts/offline integration | Temporary projects/SQLite, SDK/provider doubles and deterministic fixtures. No access to owner workspaces. |
| Browser integration | Ephemeral owner capability, temporary database and controlled media server. Never the owner's running Review Studio. |
| Native/tool proof | Explicit resource reservation, bounded source fixture and exact runtime/profile record. |
| Cloud/model proof | Explicit bounded request, recorded available model/profile and actual tool outcomes. Synthetic traces cannot substitute. |

The evidence manifest records case ID, application commit, contract revision, fixture revision, runtime/model/browser identities, command, result, artefact hashes and limits. `planned`, `synthetic_verified`, `live_pass`, `failed`, `blocked` and `owner_selected` are not interchangeable. Creative selection is always a separate owner fact.

## Documentation validation

Run the repository's documentation validator using a Python environment which already provides `jsonschema` and `PyYAML`:

```bash
python3 -B docs/validate_spec.py
```

If those modules are unavailable, use an existing suitable environment or install them only through an explicitly authorised development setup. The validator performs no generation or application startup. It validates JSON/YAML syntax, schema/reference integrity, positive/negative examples, local document links, design hashes and scenario IDs. It does not execute the future semantic/native/browser scenarios.

For optional verification of the committed reference-file hashes against the existing local checkouts:

```bash
python3 -B docs/validate_spec.py --check-references
```

`git diff --check` is the additional whitespace check. The archived HTML mockup is byte-preserved and has a non-text diff attribute; its exact bytes are checked by the design manifest.
