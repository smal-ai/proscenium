# Contract 1.0

**Normative schema revision: 1.0.0.** These files specify interfaces; they do not implement an API.

| File | Purpose |
| --- | --- |
| [schema.json](schema.json) | Closed JSON Schema 2020-12 definitions. Reference a named `$defs` entry. |
| [openapi.yaml](openapi.yaml) | OpenAPI 3.1 HTTP operations and shared wire definitions. |
| [HTTP protocol](http-api.md) | Authentication channels, idempotency, pagination, status codes and semantic checks. |
| [Event protocol](events.md) | Durable public events, native-event mapping and reconnect handshake. |
| [State transitions](state-transitions.json) | Normative work/job-attempt edges with the guards in execution.md. |
| [Operation ownership](operation-owners.json) | One backend owner/path for every HTTP operation and the shared checkpoint tool. |
| [Examples](examples/index.json) | Schema-valid and deliberately invalid synthetic payloads. |
| [Execution](../execution.md) | Normative request/work/job transitions and recovery. |
| [Publication](../source-publication.md) | Source materialisation, expected-head comparison and atomic publication. |
| [Review](../review-media.md) | Frame identity, geometry convention, uploads and annotation. |

## Rules shared by every interface

- Top-level commands/records carry `schema_version: "1.0"`. Application IDs are opaque, immutable, project-scoped identifiers. Producers should use prefixed UUIDs; fixture IDs are deliberately readable.
- Code identifiers use `artifact` while prose uses British English `artefact`.
- Dates are ISO 8601/RFC 3339 with a timezone. Presentation uses SAST. Durations and frame rates use the explicitly named units.
- JSON numbers must be finite. UTF-8 JSON is canonicalised using RFC 8785 for request/manifest fingerprints. Never hash a pretty-printed or order-dependent language representation.
- Commands are immutable. Mutable resources use integer `state_revision` or `expected_revision`, starting at zero for create. Acknowledged targets and submitted UI context cannot be edited in place.
- `null` means explicitly unavailable/not applicable. Omission means the field is optional. Unknown measurements, native IDs and usage are never synthesised as zero or success.
- All references resolve in the authenticated active-project scope. IDs, paths, `actor` and request/work references supplied by a model are not authorisation.
- Relative paths are POSIX paths. Filesystem containment, symlink resolution, graph closure, hash matching and relationships require semantic validation in addition to JSON Schema.
- Request records, native messages, work, tool invocations and external jobs have distinct IDs. Native SDK terminology must not redefine an application record silently.

## Generation of language bindings

Wire projections can include host-supplied provenance which is not written into the authored file. Copilot writes `AuthoredShotPlan` to `storyboards/<plan_id>/plan.json`. It has no `source_commit` and no `owner_selected`. `selected` there is the agent's choice and does not create an owner selection. `GET` shot-plan returns `ShotPlan`: the host adds `source_commit` and sets each frame's `owner_selected` from owner selection records. Missing provenance, an unknown licence, or the absence of an owner selection does not reject the file or block the next production step.

The foundation owner generates TypeScript wire types and Pydantic-compatible models from this schema or supplies proven equivalent generated bindings. Neither consumer maintains a hand-written parallel schema. F00 records the generator versions and a deterministic regeneration/check command. SQLite migrations remain owned by the storage component and are tested against these wire fixtures.

The schema describes the initial common contract, including interfaces delivered by later milestones. Optional capability support is reported as unavailable until implemented. A valid fixture is not proof that the corresponding feature is operational.

## Semantic validation checklist

The API/tool owner checks, at minimum:

1. Project/work/actor association and effective permission scope.
2. Every referenced record exists in that project at the requested revision.
3. Files are complete, decoded where applicable and match declared hashes/dimensions.
4. Source closure paths are unambiguous and entry/dependency references agree. A null render camera means the authored shot cameras, including animation. A camera object is a static override for that job only.
5. Frame ranges are ordered and inside the declared shot/job. The anchor frame is in its applicable range where one is supplied. A transition entry has at least two contributors. Those contributors are the sources. Do not collapse a crossfade to one invented source frame.
6. Camera matrices, projection parameters and spatial hits describe the same sampled content. Keyframe, draft and final review frames are view-transformed 8-bit sRGB PNG. An extra EXR is optional and is not the review image.
7. Masks/markup share the target image coordinate system and dimensions.
8. Publication heads, save revisions and decision fingerprints match before mutation.
9. Generation limits are reserved against the original human task/target.
10. State transitions and idempotent retries follow the execution/publication protocols.

The [semantic fixture pack](../acceptance/semantic-cases.json) supplies executable document-level positive/negative oracles for request and metadata round trips, instance selections, sequence coverage/weighted frame maps, typed owner slots and job authority. Its schema-valid negative cases must fail the named semantic invariant. `docs/validate_spec.py` also checks endpoint ownership, transition edges and mask/coordinate vectors. These checks do not establish native media existence, live cancellation, provider inference or application completion.

Errors use the shared `Error` envelope. A structural schema pass does not replace these checks or imply artistic approval.
