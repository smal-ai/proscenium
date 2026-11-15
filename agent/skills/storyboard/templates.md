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
Helper impact is conservative because contract 1.0 has plan-level asset dependencies,
not an authored per-shot dependency graph. It does not invent a parallel graph.
