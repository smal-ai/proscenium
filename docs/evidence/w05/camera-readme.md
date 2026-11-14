# Camera/catalogue targeted evidence

Accepted source base: `b907972`. See `docs/implementation/w05.md` for the precise
scope and private capture-recovery behaviour.

| File | Meaning |
| --- | --- |
| `camera-browser-result.json` | Eight actual Chromium/SwiftShader cases at DPR 1/2, including raw capture-proof attachments. |
| `camera-context-regressions.json` | Three affected existing main-chat/context/catalogue cases, Chromium only. |
| `camera-selection-dpr1.json`, `camera-selection-dpr2.json` | Actual queued `TurnSelection`, source mapping/hash, decoded pixel checks and renderer identity. |
| `camera-capture-dpr1.png`, `camera-capture-dpr2.png` | Exact PNG bytes submitted by the browser, not a recreated screenshot. |
| `camera-proxy.png` | Isolated review/outliner screenshot after recovery. Not the full desktop shell. |
| `camera-checks.json` | Exact commands, maintained source hashes, input fingerprints and limitations. |

The accepted W03 GLB and mapping are read-only inputs from
`/tmp/opencode/proscenium-w03-native-a.json` and the referenced proxy job directory.
The manifest fingerprint is cross-checked against
`/tmp/opencode/proscenium-w03-native-analysis.json`. No native application runs.
The HTTP/Queue adapters and catalogue/late-image records are fixtures, not a claim
of backend or model acceptance. The initial/late concept thumbnails are synthetic,
while **the picked GLB is the actual W03 export**.

```bash
npx playwright test --config tests/browser/review-camera.config.ts --reporter=json \
  > docs/evidence/w05/camera-browser-result.json
npx tsc --project tsconfig.tests.json
npm run typecheck
npm run lint
npm run build
```

Profiles, Vite caches, Playwright output and dependency cache stay under the home
disk cache. Do not regenerate the native proof for these tests. The parent still
owns the real Queue-to-Blender camera/framing comparison using the captured PNG
dimensions and camera. Software WebGL timings are not workstation performance data.
