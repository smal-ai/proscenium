# Brief, treatment and plan handoff

Canonical brief template:

```markdown
# <Project> brief
Goal/audience/output: <actual request>
Selected treatment: <actual direction or unresolved>
Input precedence: <pinned brief/reference/version identities>
Narrative: <beats driven by this brief, no fixed shot count>
Visual/camera/motion constraints: <known values and labelled assumptions>
Stage scope: <planning-only or admitted production stages>
Unresolved: <missing decisions/assets/images with impact and fallback>
```

## Worked example: pitch and revision

Illustrative only. Use the active brief, not these shots or timings.

**Last Train** · 12 seconds, silent. Let the audience see the train leave before
they realise someone meant to catch it. Use the tail lamp to connect the two views.

- **`carriage` · 00:00–00:05 · Departure.** Hold tight on a platform clock reflected
  in a carriage window. As the train pulls away, the red tail lamp wipes across the
  clock face. Cut on red.
- **`bench` · 00:05–00:12 · Too late.** Open wide on the same lamp receding beyond
  a bench. Rack focus to an unused ticket under a steaming cup in the foreground.
  Hold the ticket as the lamp disappears. No title: the ticket tells us enough.

“Which shot shall we refine? I'd start with `bench`: should we see the ticket before
the train has gone, or discover it a beat too late?”

**Owner:** “Discover it too late. Keep the train sharp until it's gone.”

**Reply:** **`bench` · 00:05–00:12 · Too late.** Stay wide and focused down the line
until the tail lamp vanishes. Only then pull focus to the ticket. Hold it for the
last two seconds. The red cut from `carriage` still works. “Keep this timing, or
give the empty track a longer hold before the reveal?”

## Canonical plan

Minimal **synthetic** authored shape, not a production default. Replace identities
and intent, add only fields admitted by `AuthoredShotPlan`. Null bindings are honest
until a scene is authored. The shared validator is authoritative.

```json
{
  "schema_version": "1.0",
  "plan_id": "example-plan",
  "project_id": "example-project",
  "treatment": "Unresolved: treatment to be established from the active brief",
  "brief_artifact_ids": [],
  "asset_dependencies": [],
  "shots": [{
    "shot_id": "example-shot",
    "intent": "Describe the active brief's narrative beat",
    "camera_intent": "Describe the intended camera, not an invented scene binding",
    "motion_beats": [],
    "scene_version": null,
    "source_range": null,
    "fps": null,
    "frames": [
      {"frame_id": "example-start", "role": "start", "composition": "Opening composition", "artifact_id": null, "state": "missing"},
      {"frame_id": "example-middle", "role": "intermediate", "composition": "Camera/motion transition", "artifact_id": null, "state": "proposed"},
      {"frame_id": "example-end", "role": "end", "composition": "Closing composition", "artifact_id": null, "state": "missing"}
    ]
  }],
  "unresolved": ["Brief, treatment, references, scene bindings and production timing not yet established"]
}
```

Revision handoff: exact before/after source commits and plan IDs, changed shot/frame
IDs, removed IDs, helper impact report, pinned assets and stale scene/image inputs,
unresolved work, checkpoint/publication receipts. Do not regenerate unaffected work.
Optional `Shot.planned_assets` pins exact published `kind: "asset"` `VersionRef`s for
one shot. Omit it when unrecorded or use `[]` when no pins are declared. Never turn
`motion_beats` prose into pins. Impact checks scope shot-local pins to their shots
while board-wide `asset_dependencies` conservatively affect every shot. Neither
list proves on-screen visibility. At publication, `SourceClosure.dependencies`
must include the deduplicated union of both asset sets and bound `scene_version`s.
Do not roll shot-local pins into `asset_dependencies`. An asset may derive from
board v1 via `derived_from` and be planned in board v2 without editing v1 or
forming a same-version cycle.
