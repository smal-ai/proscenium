# Run Proscenium locally

## Prerequisites

| Capability | Required workstation tools |
| --- | --- |
| Studio and saved-output review | Linux x86-64 with glibc, Python 3.12, uv, Node.js >=22.12, npm, Make, Git and Git LFS. |
| Cloud authoring | Existing supported GitHub Copilot authentication and access to the configured model. The verified login mechanism is GitHub CLI authentication. |
| Blender authoring and previews | Blender 4.5.16 LTS, build `3f6797307da5`, FFmpeg/FFprobe, and the configured Blender MCP client (`mcp-for-blender==2.3.0`) and installed add-on (2.0, protocol 16) for interactive authoring. |
| Playable Unity scenes | Unity Editor `6000.3.27f1`, with the packages declared by `tools/unity/workflow.py`. |
| Image, mesh, audio and video generation | An independently running ComfyUI service with the selected model and workflow nodes. Audio and video also require FFmpeg and FFprobe for output verification. |
| Visual critique | An independently running LM Studio vision model, identified by its exact loaded model ID. |

`make bootstrap` installs only the locked Python/Node application dependencies.
The cloud adapter pins `github-copilot-sdk==1.0.34` and `@github/copilot==1.0.112`.
It launches the direct native binary in `node_modules/@github/copilot-linux-x64/`.
Native applications, add-ons, model weights and independent services are configured
by the operator.

## Configure and start

```bash
make bootstrap
install -d -m 700 "$HOME/.config/proscenium"
cp -n config.example.json "$HOME/.config/proscenium/config.json"
chmod 600 "$HOME/.config/proscenium/config.json"
```

Edit the private configuration to match this workstation:

- `runtime.lead_model`, `worker_model` and `unity_worker_model` select available Copilot models.
- `runtime.worker_tools` lists the native and host tools exposed to `authoring-specialist`. Include `skill`, `get_project_capabilities`, `search_web`, `web_fetch`, `validate_authored_document` and `prepare_image_generation` when overriding the default list. `get_project_capabilities` reports omitted recommended names under `missing_worker_tools` without silently widening your override.
- `search_web` sends public research queries to GitHub Copilot's read-only MCP endpoint during active work. It uses `GH_TOKEN`, then `GITHUB_TOKEN`, then `gh auth token`. `web_fetch` reads selected public URLs. Neither tool acquires files into the workspace.
- `runtime.unity_editor` must be the installed Editor executable's absolute path when using Unity.
- `runtime.mcp_servers.blender` configures the existing stdio integration with `uvx --python 3.11 mcp-for-blender==2.3.0 --host 127.0.0.1 --port 9876` and `DISABLE_TELEMETRY=1`. The [Blender bridge guide](../tools/blender/README.md) shows the installed add-on and socket setup. Do not reinstall or overwrite the add-on.
- `runtime.mcp_servers.blendswap` configures hosted HTTP MCP with `type: "http"`, `url: "https://blendswap.com/api/mcp"` and `bearer_token_env: "BLENDSWAP_KEY"`. The backend reads the key privately from the process environment, `${CONFIG_DIR}/.env`, then `${APP_ROOT}/.env`, taking the first non-empty value. Dotenv files must be regular, non-symlink files no larger than 64 KiB. Values are never exported into the process environment or exposed in configuration views. Missing credentials omit this optional server without preventing a session. Never put inline tokens in `config.json` or combine this setting with an inline `Authorization` header.
- Include the matching model-facing `blender-*` and `blendswap-*` tool names in `runtime.worker_tools` so `authoring-specialist` can use delegated Blender builds and asset research. `config.example.json` lists the supported tools. Ordinary Blender authoring and free acquisition are automatic. Chargeable cloud generation tools require an explicit owner `approve_once` or `decline` decision under [owner policy, Section 2](owner-policy.md#2-automatic-operations-and-confirmations).
- `rendering.blender`, `ffmpeg` and `ffprobe` select installed executables. The default profile is four-thread Cycles CPU with a 300-second worker limit and a 60-second encode limit. Operators can set `threads` (1–32), `timeout` (1–3,600 seconds) and `encode_timeout` (1–600 seconds).
- `services` selects provider IDs, endpoints, capabilities and loaded model IDs.
- `provider_options.image_profiles` selects a versioned image profile for each Comfy provider, and optional `provider_options.image_recipes` pins exact model filenames when multiple compatible checkpoints exist on the ComfyUI server.

JSON path strings expand `${HOME}`, `${CONFIG_DIR}` and `${APP_ROOT}`. Credentials
stay in the existing supported authentication stores. Do not paste tokens into
the configuration.

```bash
make run CONFIG="$HOME/.config/proscenium/config.json" ARGS=--pair
```

Open the private pairing URL printed in the terminal. It expires after 15 minutes.
The default address is `http://127.0.0.1:8765`. Create or activate a project through
the Projects menu. The application is designed for a 1920 × 1080 desktop browser.

Run exactly one backend process. The command builds the frontend and serves it
with the API, WebSocket and media from the same origin. `--origin` and
`--frontend-dir` can override their JSON values. `--config` selects an explicit file.
Without it, the loader reads `${XDG_CONFIG_HOME:-$HOME/.config}/proscenium/config.json`.

## Independent services

ComfyUI and LM Studio remain independently installed and operated. Proscenium
does not start, stop, upgrade, unload or clear their queues. Existing media and
feedback remain accessible when a configured service is unavailable.

| Profile | Capability | Inputs |
| --- | --- | --- |
| `comfy-image-1` | `image` | FLUX.2 Klein graph and configured UNet/encoder/VAE. |
| `comfy-sd-turbo-img2img-1` | `image` | Pinned SD-Turbo F32 checkpoint and one exact 512×512 reference. |
| `comfy-sd-turbo-img2img-fp16-1` | `image` | Pinned separately prepared FP16 derivative and one exact 512×512 reference. |
| `budget3d-idol-turbo-1` | `image_to_3d` | Default 5-step Budget3D-Idol-Turbo graph (`budget3d_idol_turbo.safetensors`, `dino_v3_vit_h.safetensors`, `flux2-vae.safetensors`, `idol_vae_decoder_fp32.safetensors`, `birefnet.safetensors`) with `SplatToMesh` → `SaveGLB`. |
| `budget3d-idol-1` | `image_to_3d` | 50-step guided Budget3D-Idol graph (`budget3d_idol.safetensors` + shared encoders/decoders) with `SplatToMesh` → `SaveGLB`. |
| `hunyuan3d-native-2.0-1` | `image_to_3d` | Legacy pinned Hunyuan3D 2.0 FP16 checkpoint and untextured `VoxelToMesh` → `SaveGLB`. |
| `minimax-h3-1` | `video` | MiniMax H3 INT8 FL2VA or Ref2VA graphs for ROCm/Strix Halo. Defaults to 864×480 at 24 fps and a four-step turbo LoRA. |

For example, select explicit FLUX.2 Klein 4B and Budget3D-Idol-Turbo recipes on `local-comfy`, or select an already prepared SD-Turbo FP16 profile:

```json
{
  "image_recipes": {
    "local-comfy": {
      "profile_revision": "comfy-image-1",
      "unet": "flux-2-klein-4b.safetensors",
      "clip": "qwen_3_4b.safetensors",
      "vae": "flux2-vae.safetensors",
      "width": 1920,
      "height": 1088,
      "steps": 4,
      "max_pixels": 2088960
    }
  },
  "mesh_recipes": {
    "local-comfy": {
      "profile_revision": "budget3d-idol-turbo-1",
      "budget": 1.0,
      "mode": "synthetic",
      "steps": 5,
      "num_gaussians": 262144,
      "mesh_resolution": 384
    }
  }
}
```

```json
{"image_profiles": {"local-comfy": "comfy-sd-turbo-img2img-fp16-1"}}
```

The required checkpoint hashes and graphs are defined in
`apps/backend/src/proscenium/providers/`. FLUX.2 Klein accepts up to four distinct,
ordered `reference_artifact_ids` and scales each through `ImageScaleToTotalPixels`
(`resolution_steps: 16`, capped at 1 MP per reference). The workflow
inserts `VAEComputeDtype` (`bf16`) when advertised in ComfyUI `/object_info`, and uses
tiled VAE encode/decode only above 1 MP (`1,048,576` pixels); corrupted scanline, noise,
all-zero or multi-quadrant zeroed rasters are rejected as `corrupt_provider_output`. Add `image_to_3d`
to the Comfy provider's capabilities when the `ComfyUI-Budget3D` nodes and Budget3D/Idol
weights (or legacy Hunyuan weights under `mesh_profiles`) are installed; `SplatToMesh`
extracts vertex-coloured `.glb` geometry from the decoded 3D Gaussian splat. New
requests freeze the profile, endpoint, graph and input hashes. Configuration changes do
not reinterpret admitted jobs. The default attempt budget is three per human task and
target.

### MiniMax H3 video on ROCm 10.1 / Strix Halo

Enable `video` in the ComfyUI service's capabilities. The following
`provider_options` excerpt uses the exact standard filenames and recipe limits
from `config.example.json`. Replace `local-comfy` only if the service uses a
different provider ID.

```json
{
  "video_recipes": {
    "local-comfy": {
      "profile_revision": "minimax-h3-1",
      "fl2va_model": "minimax_h3_fl2va_pruned_int8_convrot.safetensors",
      "ref2va_model": "minimax_h3_ref2va_pruned_int8_convrot.safetensors",
      "clip": "qwen3vl_32b_minimax_h3_int8_convrot.safetensors",
      "video_vae": "minimax_h3_video_vae_fp16.safetensors",
      "audio_vae": "minimax_h3_audio_vae_fp32.safetensors",
      "fl2va_lora_4step": "minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors",
      "fl2va_lora_8step": "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors",
      "ref2va_lora_4step": "minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors",
      "width": 864,
      "height": 480,
      "steps": 4,
      "max_pixels": 1032192,
      "max_duration_seconds": 15.0,
      "tiled_vae": false,
      "shift_video": 12.0,
      "shift_audio": 3.0
    }
  }
}
```

- On the Radeon 8060S (`gfx1151`), use the AMD-compatible `int8_convrot` denoisers
  and text encoder, not NVIDIA `nvfp4` weights.
- The host wraps the video VAE in `VAEComputeDtype` (`bf16`) only when ComfyUI
  `/object_info` advertises compatible BF16 support. A ready provider can still
  omit that node. The on-disk video checkpoint is named `fp16`. The separate
  `minimax_h3_audio_vae_fp32` stays in FP32 and is never wrapped in the BF16 node.
- The host requires `VAEDecodeTiled` above `864 × 480` pixels or 124 video frames.
  `tiled_vae: false` does not override this threshold. Setting it to `true` also
  tiles smaller requests.

For a dedicated Strix Halo ComfyUI instance, start with these operator-managed
settings. Use the existing ROCm environment and bind address:

```bash
COMFY_KITCHEN_DISABLE_HIP=1 \
PYTORCH_HIP_ALLOC_CONF=expandable_segments:True \
MIOPEN_FIND_MODE=FAST \
python main.py --bf16-vae --disable-mmap --cache-none
```

Use `--highvram` only when there is measured memory headroom to keep the model
resident. It controls residency, while `--cache-none` controls node result caching.
Proscenium does not apply launch flags or manage ComfyUI. The capability probe
checks the installed models and node choices, and requires local FFmpeg and
FFprobe. It does not run inference or prove that a requested canvas fits.

Send the exact `submit_request` returned by `prepare_video_generation` to
`submit_generation`. FL2VA accepts first, last and indexed guide frames. Ref2VA
accepts ordered image references and an optional first-frame guide.

Publish a `SequenceMap` with only clip entries and one-based source frame ranges,
then call `assemble_sequence`. Use `silent: true` for a silent cut,
`preserve_clip_audio: true` to retain native sound and optionally mix external
tracks, or `silent: false` with external `audio_tracks` to replace native sound.
Clip sequences do not use a render `FrameMap`.

### Capability status

**Services & models** shows bounded live provider catalogue/recipe observations,
configured workstation bridges and a manual **Refresh status** control. These reads
do not start services, perform inference or reserve generation budgets. A listed
VLM model is not proof of vision inference. Failed probes leave profile editing available.
Create, edit and select owner-controlled Copilot lead/worker profiles in the same
menu. Profiles persist privately in `registry.sqlite3`, independently of source
configuration. New Queue requests freeze their selected model identities. A profile
selected by any registered project cannot be deleted, and one authoring profile must remain.

`services[].available` and `profiles[].scope_status` remain configured metadata.
The 256 MiB free-VRAM check is a
capability preflight, not a guarantee that a model fits in memory.

The accepted SD-Turbo correction workflow did not achieve its requested blue
colour. The measured 90 MB mesh proxy was slow under SwiftShader. These original
[quality and performance observations](https://github.com/smal-ai/proscenium/blob/6b714c0dd574abe3995cf7020b988190336cefdf/docs/implementation/performance.md)
remain relevant when choosing models and review workloads.

## SSH access

```bash
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:8765:127.0.0.1:8765 USER@WORKSTATION
```

Open `http://127.0.0.1:8765` on the client. Keep its scheme, host and port identical
to the configured origin. API, WebSocket and media use this forwarded origin.
Browser/tunnel disconnection leaves admitted work running.

## Restart and persistence

- The machine registry defaults to `${XDG_DATA_HOME:-$HOME/.local/share}/proscenium`.
- Each project keeps its application records and retained media under `.proscenium/`.
- Git/Git LFS retain authored source and pinned dependencies. They do not replace backups of annotations, published media or native conversation history.
- Restart and unsolicited native aborts restore read models with execution held. Use **Resume** deliberately (`Reconcile only`, `Continue original work` or `Release held queue`) and await `getResumeOperation` completion.
- **Stop** controls Copilot, including post-restart cold RPC inactivity reconciliation when prior tool invocations have settled. External jobs have separate cancellation support. A missing native result stays unknown.

## Backup and restore

To register an existing Git/LFS project and verified external media, stop the relevant
backend and use the [owner import CLI](project-import.md) before reopening Studio.

First settle/stop the relevant work through its normal controls, then stop that
Proscenium backend. Preserve unrelated native sessions. Backup requires quiescent
source and native-history stores and a fresh destination outside them.

```bash
uv run python -m tools.operations.recovery backup \
  --config "$HOME/.config/proscenium/config.json" \
  --project-id PROJECT_ID --target /absolute/private/backup
uv run python -m tools.operations.recovery verify /absolute/private/backup
uv run python -m tools.operations.recovery restore /absolute/private/backup \
  --target /absolute/private/restored --rebind-fresh-registry
```

Use the generated restore configuration to inspect the restored project before
explicit Resume. Do not run both locations with the same project/native-session
identity. Backup preserves the original source files and copies associated
Git/LFS, SQLite, media and native history through validated read-only paths.
Incomplete/corrupt or busy stores are reported explicitly. Backups are private
material and are not automatically encrypted or uploaded.
Owner-created profile definitions are retained in the backed-up machine registry
alongside the selected project's profile preference. Authentication and unrelated
project records remain excluded.

The runtime identity manifest at `docs/implementation/runtime-lock.json` is retained
because the backup tool reads it. The verified native relocation used the same
workstation with the old source directory present. Arbitrary old opaque attachment
paths on another machine remain untested.

An optional [user-service template](../deployment/proscenium.service.in) is provided.
Install it only with reviewed absolute checkout/configuration paths. It starts one
backend, without automatic pairing output or Resume.
