# Proscenium

An AI-powered desktop studio for creating 3D assets, storyboards, silent cinematics and playable Unity scenes through conversation and visual feedback.

## Run locally

Use Linux x86-64, Python 3.12, [uv](https://docs.astral.sh/uv/), Node.js 22.12 or newer, npm, Make, Git and Git LFS.

```bash
git clone --single-branch --branch main https://github.com/smal-ai/proscenium.git
cd proscenium
make bootstrap
install -d -m 700 "$HOME/.config/proscenium"
cp -n config.example.json "$HOME/.config/proscenium/config.json"
chmod 600 "$HOME/.config/proscenium/config.json"
```

Configure the installed native tools, Copilot models and independent services in that private JSON file, then start:

```bash
make run CONFIG="$HOME/.config/proscenium/config.json" ARGS=--pair
```

Open the private pairing URL printed in the terminal. The studio serves its UI, API, WebSocket and media at **http://127.0.0.1:8765**. Use one backend process and a desktop Chromium, Brave or Edge browser.

[Studio guide](docs/studio.md) · [Operator guide](docs/operator.md) · [Runtime specification](docs/specification.md) · [Owner policy](docs/owner-policy.md)

## Included capabilities

- Resizable Split view and Chat focus studio with persistent project Conversation, independent Request targeting, Reference versions, Queue, Steer, Stop and explicit Resume.
- Asset-first, scene-first and storyboard-first workflows with Blender authoring, static 3D inspection, rendered keyframes and progressive animation review.
- Host-validated canonical storyboards, deterministic ComfyUI image workflow preparation, budgeted image generation, actual image-to-3D mesh generation and retained-output previews when publication is blocked.
- Requested VLM critique with owner-approved feedback, silent cinematic assembly and playable Unity scenes.
- Git/Git LFS source versions, pinned dependencies, local backup/restore and SSH access.

Copilot access and the native/model tools are workstation prerequisites. ComfyUI and LM Studio run independently. Saved outputs remain reviewable when those services are unavailable.

## Development

```bash
make check
make test-browser
```

`make check` verifies formatting, lint, types, generated contracts, the frontend build, frontend unit tests and offline Python/runtime tests. `make test-browser` runs the isolated Playwright Chromium suite against a hermetic fake API with external execution blocked. Use a topic branch and a pull request for changes to protected `main`.

`main` originated as a selective runtime import of [`6b714c0`](https://github.com/smal-ai/proscenium/commit/6b714c0dd574abe3995cf7020b988190336cefdf), evolved through reviewed pull requests. Historical milestone proofs and release evidence remain on [`impl/studio`](https://github.com/smal-ai/proscenium/tree/impl/studio/docs/evidence/release).
