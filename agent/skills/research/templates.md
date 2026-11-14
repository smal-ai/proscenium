# Maintained research templates

Copy only useful sections into the active project's authored documentation. These
are working notes, not additional API schemas or completed evidence.

## Brief and coverage

```markdown
# <Topic> research
Project ID and active canonical root: <host-provided>
Request scope and selected treatment: <actual brief and pinned artefacts>
Date/timezone: <ISO 8601 +02:00>
Output/fidelity/application/formats: <known values or explicit unknown>
Storage cap: <originals + extraction + derivatives>
Inputs and precedence: <exact version/hash, current direction versus history>

| Requirement/shot ID | Asset/action and viewing distance | Local evidence/version IDs | Construction/scale/material/motion facts | Acquisition or authoring route | Checks run | Remaining input |
| --- | --- | --- | --- | --- | --- | --- |

## Unresolved decisions
| Question | Impact | Independent work that continues | Resolution owner |
| --- | --- | --- | --- |
```

## Acquisition notes

For each lead retain a stable research ID, requirement IDs, discovered/acquired/
imported/validated state, project-relative destination, source page, retrieval time,
actual bytes/hash where acquired, and exact tool/application evidence. Use the shared
`Provenance` object in catalogue/publication requests:

```json
{"source_url": null, "creator": null, "licence": null}
```

Retain attribution and restrictions as evidence, not permission flags. Record archive
parent/member and derivative input identities separately. Failed/partial downloads
do not count as acquired. Unknown metadata stays null, not an invented fact.

## Build handoff

```markdown
## Outcome
What exists locally, what imported, what was actually validated, remaining limits.
## Build guidance
Hierarchy, axes/origin/units, silhouette, dimensional anchors versus assumptions,
materials, texture dependencies, contacts/pivots, camera needs and simplifications.
## Readiness
| Requirement | Exact evidence/version | Remaining risk | Next proof/fallback |
| --- | --- | --- | --- |
## Source handoff
Owned source paths, checkpoint operation/receipt, source commit, dependency versions,
publication receipt and any unavailable configured capability. No inferred success.
## Next stage
Smallest useful next action within the admitted scope. Planning alone launches none.
```
