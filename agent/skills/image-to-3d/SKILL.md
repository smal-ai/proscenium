---
name: image-to-3d
description: Prepare an image-conditioned Budget3D mesh workflow and use the accepted budgeted image_to_3d provider when available. Never substitute procedural geometry or preview images for a generated mesh.
---

# Image to 3D

- Call `get_project_capabilities` first. Honour host `request_target`, pinned
  `reference_versions` and `execution_scope`. Planning-only work performs no
  generation, preview rendering or scene execution. `prepare_image_generation`
  prepares 2D image concepts only, never a 3D mesh workflow.
- Validate authored plans, sequences and publications with `validate_authored_document`
  before checkpoint/publication. `compare_to_path` and `changed_ids` report plan impact.
  Publish a standalone generated/imported `asset` without a storyboard. Existing
  dependent scenes/storyboards keep version pins until explicit adoption.

- Work only in the active project and the current human generation task.
- Check the configured provider capability (`capability: "image_to_3d"`). The host-owned
  Budget3D plugin (`budget3d-idol-turbo-1` 5-step default, or `budget3d-idol-1` 50-step;
  legacy `hunyuan3d-native-2.0-1` retained only when explicitly configured) supports
  `image_to_3d` only with its exact trusted graph and `selected_models`. If unavailable,
  report the exact blocker. Do not call ComfyUI `/prompt` through shell, Python or a
  diagnostic CLI to bypass the budgeted adapter.
- Register the conditioning image (`kind: "image"`, `purpose: "reference"`). Call
  `prepare_mesh_generation` with `schema_version: "1.0"`, a stable `operation_id`, the
  registered `reference_artifact_id`, `prompt` and a JSON-safe decimal `seed` string.
  Omit `project_id` and `generation_task_id` so the host binds them to active work.
- Use the configured recipe defaults unless a different setting is requested. Budget3D
  accepts `mode` (`"synthetic"` for renders or `"real"` for photographs), `budget`
  (0.0625–1.0), `steps` (1–50), `num_gaussians` (32768–1048576 in multiples of 32)
  and `mesh_resolution` (64–768 in multiples of 16). Hunyuan3D accepts none of these overrides.
- Preparation preflights the installed nodes and models and registers the trusted workflow
  and prompt provenance without running inference. Call `submit_generation` with the
  **exact returned `submit_request`**. Do not write a workflow graph in shell or Python.
- Manual compatibility: existing callers can submit an already registered, pinned workflow
  and prompt with `capability: "image_to_3d"` and the exact `provider_id`. Copy
  `selected_models` from `get_project_capabilities` into `submit_generation`'s `model_ids`
  field. Send one `input_artifact_ids` entry and `settings: {}` when there is no target slot. For an
  unpublished asset use `target_id == generation_task_id`, or the active new-item intent ID.
  In a Budget3D graph `SaveGLB.filename_prefix` must be the literal `"mesh/proscenium-budget3d"`.

- Budget3D is image-conditioned (`BiRefNet` foreground segmentation + `DINOv3 ViT-H+/16` +
  `FLUX.2 VAE`). Prompt text is provenance, not text conditioning. New inferred geometry
  needs corresponding image conditioning or another proven model capability. Ordinary scale,
  placement, material, camera and light revisions are Blender authoring of the existing
  mesh and consume no generation attempt.
- Use one shared attempt reservation and resource claim. Wait, inspect history and
  retrieve the original prompt's output without another attempt. Reconcile uncertain
  acceptance before considering a correction. Never reset a budget by renaming a target.
- Do not install weights/plugins, change independent service configuration, restart
  services or unload independent models under routine generation authority.
- A successful job must provide actual GLB mesh bytes with retained model, workflow,
  input/derivative hashes and prompt identity. A thumbnail, image plane, download link or
  procedural substitute is not successful image-to-3D.
- Inspect/import into Blender using an owned active-project workflow. Record finite
  geometry, bounds, scale, origin, stable identity, vertex colours (`COLOR_0`), material
  and texture availability. Preserve the original mesh, save editable Blender source,
  checkpoint with the accepted source tool and publish an unreviewed asset through the
  normal publication API.
- State observed limitations. Budget3D reconstructs 3D Gaussian splats converted via
  narrow-band iso-surface extraction (`SplatToMesh`) with baked vertex colours (`COLOR_0`),
  not clean quad topology, UV unwraps or rigging. Agent inspection never becomes owner
  selection.
