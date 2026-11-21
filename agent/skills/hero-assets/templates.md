# Hero-asset example and spec template

## Worked example: The Tide Signal

Illustrative only. Use actual project IDs and images, never these fixtures.

**Complete shortlist**

| Asset | Shots | Why bespoke | Catalogue match | Reference candidates |
| --- | --- | --- | --- | --- |
| `tide-key` · Tide-gate winding key | `boathouse-wide`, `key-insert`, `gate-playable` | Its forked outline identifies it across shots. A notch aligns with the spindle so the player knows how to fit it. | No match. | `panel-key-insert` shows the fork and blue oxide. A director-supplied profile sketch could settle the handle shape. |
| `tide-dial` · Enamel tide dial | `dial-reveal`, `gate-playable` | The player reads the high-water mark to decide when to open the gate. | `harbour-dial` is a possible reuse. Check whether its graduations can carry the clue. | `panel-dial-reveal` shows the face and palette. An emblem drawing would help if it needs new markings. |

The boathouse timbers remain modular scenery. “Shall we start with `tide-key`,
or would you prefer `tide-dial`?”

**Director:** “Start with the key. Use the profile sketch I attached.”

**Single definition card: `tide-key`**

- **Observed:** `panel-key-insert` shows the fork and blue oxide on the front
  face. No panel reveals the underside.
- **Proposed:** A 28 cm forged steel key with a loop handle from the sketch and a
  shallow underside alignment notch. Pitting stays off the spindle contact faces.
- **Clue and continuity:** The fork must read in the wide shot. Blue oxide and
  the notch must remain readable in the insert and on the Unity interaction mesh.
  Keep the spindle fit and the grip consistent at the playable handoff.
- **View:** One clean three-quarter view on neutral grey, angled to show the notch.
- **References:** `image 1` = `sketch-tide-key` for silhouette and proportions.
  `image 2` = `panel-key-insert` for oxide colour, steel wear and edge highlights.

“Keep the underside notch, or put the mark on the face visible in the insert?”

**Director:** “Keep the underside notch. Generate one concept using both images.”

**Generation handoff:** Check both artefacts are ready inline and confirm the
request permits inference and the selected profile is `comfy-image-1`. For this
standalone concept, use the active asset creation intent or project task. Example
`prepare_image_generation` body, without an unpublished `target_id`:

```json
{
  "schema_version": "1.0",
  "operation_id": "tide-key-concept-01",
  "prompt": "Standalone 28 cm forged tide-gate winding key, clean three-quarter view on neutral grey. Use image 1 for the forked silhouette, grip and proportions. Use image 2 for blue oxide, steel wear and edge highlights only, not the shot composition. Show the shallow underside spindle notch. No scenery or text.",
  "seed": "42",
  "reference_artifact_ids": ["sketch-tide-key", "panel-key-insert"]
}
```

Submit the exact returned request. Wait, inspect and compare against both
references in a VLM review when configured. Publish the verified concept with the
agreed spec as an asset, not a frame update or implied owner selection.

## `assets/<asset_id>/spec.md` template

```markdown
# <Asset title>

- Asset ID: <stable asset_id>
- Storyboard: <pinned item_id and version_id, or unpublished plan_id>
- Shot IDs and hero purpose: <where it appears and why it must read or be usable>
- Catalogue reuse: <matched item and version, reuse decision, or no match>
- Observed evidence: <panel/attachment IDs and what is actually visible>
- Proposed silhouette, proportions and scale: <include unseen angles>
- Materials, colour and condition: <surfaces, wear and limits>
- Readable clue or gameplay affordance: <including interaction/Unity needs>
- Continuity: <wide, close-up and playable handoff constraints>
- Concept view: <angle, lighting and ground>
- Ordered references: <image 1: artefact ID + role, image 2: artefact ID + role>
- Agreed decisions and open gaps: <director decisions and unresolved evidence>
```
