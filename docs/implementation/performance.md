# Workload-scoped performance measurements

## M14 viewport allocation

Base `0c77d899ce008a6502c2a4a4265d42ab794e3542`. This task measures the maintained `ProxyView` component in a controlled compiled fixture. It is not a new backend/native journey, whole-studio shell benchmark, browser matrix or M14/release acceptance.

Preflight builds the maintained studio and a fixture importing the unmodified `ProxyView` and its normal `useGLTF`, Canvas, OrbitControls, CameraObserver and CaptureBinding implementation. The fixture has a 1600 × 900 canvas inside a 1920 × 1080/DPR1 viewport. Only the exact hash-verified small Geometry Nodes GLB/mapping and accepted old M10-v1 GLB/mapping are copied into a fresh owned home-cache fixture. M10 SQLite, source and session state are never read. No SDK, authoring, export, inference, native encoder, service operation or geometry reduction is part of this allocation.

One disposable Chromium profile uses the accepted SwiftShader flags. Small then heavy load once, without an HTTP cache, in the same browser. Shader/JavaScript state and the small asset can remain warm for the second case. A normal maintained framebuffer capture verifies shaded geometry and changed camera/pixels after scripted pointer orbit before sampling.

The observation hook forwards actual browser rAF callbacks and GL draw calls unchanged. It retains timestamps only for rAF callbacks that issued a GL draw, with JavaScript callback duration and draw count. It does **not** call `gl.finish`, use a GPU fence, time GPU completion or measure compositor presentation. Prospective targets will be derived from the observations and labelled for this software-rendered fixture, not declared universal SLAs.

Guards: four GiB available host RAM, two GiB **summed attributed Chromium-process RSS** (not PSS, so shared pages can be counted more than once), one browser launch/profile, two cases and 240 seconds total after preflight. The safety shutdown begins at 235 seconds. Exact PID/start/parent/process-group identities attribute the root and descendants, including previously observed orphans. Independent processes are neither signalled nor included. A guard stop retains actual partial samples rather than padding to 120/60 callbacks. No resource wait or guard override is allowed.

Preflight commands:

```bash
uv sync --frozen
npm ci --cache "$HOME/.cache/proscenium-development/npm" --ignore-scripts
npm run build
node --test tests/acceptance/m14_performance_helpers.test.mjs
node --check tests/native/studio/m14_performance.mjs
node_modules/.bin/tsc --noEmit --jsx react-jsx --module ESNext \
  --moduleResolution bundler --target ES2022 --lib ES2022,DOM \
  --esModuleInterop --skipLibCheck tests/acceptance/m14_performance_fixture.tsx
node tests/native/studio/m14_performance.mjs prepare \
  "$HOME/.cache/proscenium-development/milestones/m14-performance-20261114-a"
```

The compiled studio and fixture builds, pure helper tests and fixture typecheck passed before measurement. Existing Lucide directive and large-bundle warnings remain visible. Exact source, lockfile, copied proxy/mapping and built asset fingerprints are retained by the driver. The measured outcome and historical renderer/provider rows are recorded after execution, not assumed by this preflight document.
