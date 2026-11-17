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
- Register the actual conditioning image (`kind: "image"`, `purpose: "reference"`), the
  trusted Budget3D API workflow JSON (`kind: "document"`, `purpose: "metadata"`,
  `media_type: "application/json"`) and prompt provenance (`kind: "document"`,
  `purpose: "metadata"`, `media_type: "text/plain; charset=utf-8"`) as project artefacts.
  Submit via `submit_generation` with `capability: "image_to_3d"`, the exact
  `selected_models` from `get_project_capabilities`, one `input_artifact_ids` entry
  (`[REFERENCE_ID]`) and a decimal `seed`.
- Trusted Budget3D API workflow template (`REFERENCE_ID` matches the registered
  conditioning artefact ID; for `budget3d-idol-turbo-1` use
  `unet_name: "budget3d_idol_turbo.safetensors"`, `steps: 5`, `cfg: 0.0`; for
  `budget3d-idol-1` use `unet_name: "budget3d_idol.safetensors"`, `steps: 50`, `cfg: 3.0`;
  `mode` is `"synthetic"` for renders/illustrations or `"real"` for photographs; `budget`
  is `0.0625..1.0`):

```json
{
  "1": {"class_type": "LoadImage", "inputs": {"image": "artifact:REFERENCE_ID"}},
  "2": {"class_type": "LoadBackgroundRemovalModel", "inputs": {"bg_removal_name": "birefnet.safetensors"}},
  "3": {"class_type": "RemoveBackground", "inputs": {"bg_removal_model": ["2", 0], "image": ["1", 0]}},
  "4": {"class_type": "Budget3DPreprocessImage", "inputs": {"image": ["1", 0], "mask": ["3", 0], "mode": "synthetic"}},
  "5": {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": "dino_v3_vit_h.safetensors"}},
  "6": {"class_type": "VAELoader", "inputs": {"vae_name": "flux2-vae.safetensors"}},
  "7": {"class_type": "VAEComputeDtype", "inputs": {"vae": ["6", 0], "dtype": "bf16"}},
  "8": {"class_type": "TripoSplatConditioning", "inputs": {"clip_vision": ["5", 0], "vae": ["7", 0], "image": ["4", 0]}},
  "9": {"class_type": "Budget3DLoader", "inputs": {"unet_name": "budget3d_idol_turbo.safetensors"}},
  "10": {"class_type": "Budget3DSampler", "inputs": {"budget3d_model": ["9", 0], "positive": ["8", 0], "negative": ["8", 1], "latent_image": ["8", 2], "seed": "$seed", "steps": 5, "cfg": 0.0, "budget": 1.0, "rescale_t": 3.0, "warmup_steps": 0}},
  "11": {"class_type": "VAELoader", "inputs": {"vae_name": "idol_vae_decoder_fp32.safetensors"}},
  "12": {"class_type": "VAEComputeDtype", "inputs": {"vae": ["11", 0], "dtype": "fp32"}},
  "13": {"class_type": "VAEDecodeTripoSplat", "inputs": {"samples": ["10", 0], "vae": ["12", 0], "num_gaussians": 262144, "seed": "$seed"}},
  "14": {"class_type": "SplatToMesh", "inputs": {"splat": ["13", 0], "resolution": 384, "kernel": 5, "smooth": 3, "level": 0.4, "min_component": 500, "min_opacity": 0.02, "color_sharpen": 2.0, "solid": true, "fill": 20.0}},
  "15": {"class_type": "SaveGLB", "inputs": {"mesh": ["14", 0], "filename_prefix": "mesh/proscenium-budget3d"}}
}
```

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
