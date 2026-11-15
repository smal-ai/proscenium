# Run Proscenium locally

## Prerequisites

| Capability | Required workstation tools |
| --- | --- |
| Studio and saved-output review | Linux x86-64 with glibc, Python 3.12, uv, Node.js >=22.12, npm, Make, Git and Git LFS. |
| Cloud authoring | Existing supported GitHub Copilot authentication and access to the configured model. The verified login mechanism is GitHub CLI authentication. |
| Blender authoring and previews | Blender 4.5.14 LTS, build `62c1db4208e8`, FFmpeg/FFprobe, and the configured Blender MCP client/add-on for interactive authoring. |
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
- `runtime.unity_editor` must be the installed Editor executable's absolute path when using Unity.
- `runtime.mcp_servers` configures the existing MCP integrations. The [Blender bridge guide](../tools/blender/README.md) shows the pinned client and socket setup.
- `rendering.blender`, `ffmpeg` and `ffprobe` select installed executables. The default preview profile is four-thread Cycles CPU with a 300-second worker limit.
- `services` selects provider IDs, endpoints, capabilities and loaded model IDs.
- `provider_options.image_profiles` selects a versioned image profile for each Comfy provider.

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

| Image profile | Inputs |
| --- | --- |
| `comfy-image-1` | FLUX.2 Klein graph and configured UNet/encoder/VAE. |
| `comfy-sd-turbo-img2img-1` | Pinned SD-Turbo F32 checkpoint and one exact 512×512 reference. |
| `comfy-sd-turbo-img2img-fp16-1` | Pinned separately prepared FP16 derivative and one exact 512×512 reference. |

For example, add this key to `provider_options` for an already prepared FP16 service:

```json
{"image_profiles": {"local-comfy": "comfy-sd-turbo-img2img-fp16-1"}}
```

The required checkpoint hashes and graphs are defined in
`apps/backend/src/proscenium/providers/`. Add `image_to_3d` to the Comfy provider's
capabilities when the Hunyuan model/nodes are installed. New requests freeze the
profile, endpoint, graph and input hashes. Configuration changes do not reinterpret
admitted jobs. The default attempt budget is three per human task and target.

`services[].available` and `profiles[].scope_status` are configured metadata.
Unknown availability is shown explicitly. The 256 MiB free-VRAM check is a
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
- Restart restores read models with execution held. Use **Resume** deliberately to reconcile original work or release held Queue requests.
- **Stop** controls Copilot. External jobs have separate cancellation support. A missing native result stays unknown.

## Backup and restore

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

The runtime identity manifest at `docs/implementation/runtime-lock.json` is retained
because the backup tool reads it. The verified native relocation used the same
workstation with the old source directory present. Arbitrary old opaque attachment
paths on another machine remain untested.

An optional [user-service template](../deployment/proscenium.service.in) is provided.
Install it only with reviewed absolute checkout/configuration paths. It starts one
backend, without automatic pairing output or Resume.
