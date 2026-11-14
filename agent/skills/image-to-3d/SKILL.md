---
name: image-to-3d
description: Prepare an image-conditioned mesh workflow and use the accepted budgeted image_to_3d provider when available. Never substitute procedural geometry or preview images for a generated mesh.
---

# Image to 3D

- Work only in the active project and the current human generation task.
- Check the configured provider capability. The host-owned Hunyuan plugin supports
  `image_to_3d` only with its exact trusted graph and model. If unavailable, report the exact
  blocker. Do not call ComfyUI
  `/prompt` through shell, Python or a diagnostic CLI to bypass the budgeted adapter.
- Register the actual conditioning image, trusted API workflow and required prompt
  provenance as project artefacts. Use existing `GenerationSubmit` with capability
  `image_to_3d`, the original task/target, exact model IDs and the accepted target slot policy.
- The Hunyuan3D 2.0 graph is image-only. Prompt text is provenance, not text conditioning.
  New inferred geometry needs corresponding image conditioning or another proven model
  capability. Ordinary scale, placement, material, camera and light revisions are Blender
  authoring of the existing mesh. They need no new inference or generation reservation.
  Do not regenerate merely because the owner requests an ordinary editable-source revision.
- Use one shared attempt reservation and resource claim. Wait, inspect history and
  retrieve the original prompt's output without another attempt. Reconcile uncertain
  acceptance before considering a correction. Never reset a budget by renaming a target.
- Do not install weights/plugins, change independent service configuration, restart
  services or unload independent models under routine generation authority.
- A successful job must provide actual GLB mesh bytes with retained model, workflow,
  input/derivative hashes and prompt identity. A thumbnail, image plane, download link or
  procedural substitute is not successful image-to-3D.
- Inspect/import into Blender using an owned active-project workflow. Record finite
  geometry, bounds, scale, origin, stable identity, material and texture availability.
  Preserve the original mesh, save editable Blender source, checkpoint with the accepted
  source tool and publish an unreviewed asset through the normal publication API.
- State observed limitations. This candidate produces untextured shapes, not proven
  clean topology, retopology or rigging. Agent inspection never becomes owner selection.

The provider plugin and accepted E04 hardware proof are documented in
`docs/implementation/w09.md`. Preparation commands construct/check data and do not submit
inference. Imported source copies retain original mesh/image/model/graph/checkpoint
provenance. Preserve previous source versions and stable object/instance IDs when revising.
