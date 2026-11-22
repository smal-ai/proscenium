# Run Proscenium locally

## Prerequisites

| Capability | Required workstation tools |
| --- | --- |
| Studio and saved-output review | Linux x86-64 with glibc, Python 3.12, uv, Node.js >=22.12, npm, Make, Git and Git LFS. |
| Cloud authoring | Existing supported GitHub Copilot authentication and access to the configured model. The verified login mechanism is GitHub CLI authentication. |
| Blender authoring and previews | Blender 4.5.16 LTS, build `3f6797307da5`, FFmpeg/FFprobe, and the configured Blender MCP client (`mcp-for-blender==2.3.0`) and installed add-on (2.0, protocol 16) for interactive authoring. |
| Playable Unity scenes | Unity Editor `6000.3.27f1`, with the packages declared by `tools/unity/workflow.py`. |
| Image/mesh generation | An independently running ComfyUI service with the selected model and workflow nodes. |
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
