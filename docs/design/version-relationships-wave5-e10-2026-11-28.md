# Wave 5: E10 native Blender proof

**Result:** The historical-source, pre-edit refusal, static proxy and draft motion
checks passed on `feat/version-relationships` on 28 November 2026 (SAST). This is a
bounded technical fixture, not animation quality or production release acceptance.
See the [E10 gate](version-relationships.md#9-acceptance-tests) and
[Wave 4 recovery evidence](version-relationships-wave4-2026-11-28.md).

## Allocation and method

- AMD Ryzen AI Max+ 395, Linux x86-64, Blender `4.5.16 LTS` build `3f6797307da5`
  (built `2026-11-10 01:31:21`), installed `bl_ext.blender_org.mpfb` `2.0.18`
  (MPFB build `20261010`). One owned disposable `/tmp/opencode/e10-proof-*`
  directory on tmpfs, one Blender process at a time, `--threads 4`, 300-second
  timeout per attempt. Blender ran in a separate network namespace with a private
  `/dev` (no GPU devices), a read-only host filesystem and only the disposable
  directory writable. No external uploads, service changes or native session reuse.
- MPFB add-on and selected local pack files were copied into the disposable
  configuration. Verified its extension and user-data roots and that secondary
  and MakeHuman roots were unset. Created one masked body and fitted outfit with
  the 52-bone `mixamo` rig in `HeroRig`. The body and outfit had 205 and 22 vertex
  groups and armature modifiers. Both materials and all four external 2048²
  images (skin diffuse and outfit AO, diffuse and normal) reopened packed.
  The local `makehuman_system_assets` manifest records `male_casualsuit01` and
  `young_caucasian_male` as `makehuman_system`, `CC0`, source
  `http://www.makehumancommunity.org`, empty original author/source. No aggregate
  licence or external motion was asserted for the fixture.
- In a separate Action-only library, keyed hips, both thighs, knees, feet, arms
  and forearms. `Walk`, `Run`, `StairsUp`, `StairsDown` each reopened with one
  slot, 33 curves and a fake user in the producer. They had 99, 99, 132 and 66
  keyframe points respectively. The scene used two independent editable
  hierarchies created with `override_hierarchy_create`. Instance A played
  `Walk` at frames 1–3 then `Run` at 4–6. Instance B played `StairsUp` at 1–4
  then `StairsDown` at 5–6. Reopened from `--factory-startup --disable-autoexec`
  without MPFB loaded and verified these NLA strips, both override hierarchies,
  their materials and stable object/instance IDs. The scene also contained the
  landing, three treads, a camera and three area lights.
- Used the disposable checkpoint fixture with real local Git/Git LFS, `Sources`,
  `Catalogue`, `PreviewService.request`, the preview launcher and supervisor, and
  job reconciliation. No web backend or provider was started. Each preview ran
  the actual [`blender_worker.py`](../../apps/backend/src/proscenium/previews/blender_worker.py)
  (`f04a92b0e2fe550e150bcdae5d85d10d704bd0497d2456547dc6a8adb4ad6a85`)
  with `--background --factory-startup --disable-autoexec --threads 4` on its own
  `Sources.materialise` snapshot. Verified the four snapshot files against the
  closure before inspection and verified both job snapshots after native exit.

## Source and negative-case evidence

Project `project-one`, repository `repository-one`. Publication IDs were
`publish-<version_id>` and checkpoint operation IDs matched `version_id` in this fixture.

| Item / version | Source commit | Closure SHA-256 |
| --- | --- | --- |
| `hero / hero-v3` (`character`) | `a76cae77f2cea82139457f577a72040e8c0a3a3d` | `4f256176a57b21a50b26f3144d8c9695d28e303f9fdacd1bfc96958872121a64` |
| `locomotion / loco-v1` (`motion`) | `fe3c3fd42b79d6371d0dc19bcfa9428eb5aaa995` | `890a24d1cb0d9bf0a75e3281158b735d75f207d387f18724533898806f44251c` |
| `stairwell / stairwell-v1` | `2b17cf6d61d978360c7bbd77265826f2dad1c258` | `cf02b48886a72d88897c48fc3c8bf4d92a4337f66caeb30bed87a36b188107dc` |
| `hero / hero-v4` (`character`) | `82a8d9e4d1145a826d7f53f34232b2a81ad444cb` | `05c808fc81d0f58a3869b0797d4db198117402e40fd7c0ff586e907e3c73811d` |

Stairwell owned only the scene file. It included `hero-v3` and `loco-v1` at
their original destinations, commits and hashes. The Blender library paths
were `//../../assets/hero/source/hero.blend` and
`//../../assets/locomotion/source/locomotion.blend`.

| Sealed `stairwell-v1` destination | Bytes | Hydrated SHA-256 |
| --- | ---: | --- |
| `assets/hero/source/hero.blend` | 18,171,287 | `92e5d60fc38ee1ad490d4785311684ec2c45d9774a695905dccf0a07a0028c4b` |
| `assets/hero/source/human.hero.json` | 1,232 | `187d8ec4d110c5f6212b9bddb0ba48eafae3f8d3b28105345b9fcab944c12725` |
| `assets/locomotion/source/locomotion.blend` | 205,932 | `fe12972f04986208366140724c11a9960c8f896d6b9261159717b6dfda2be018` |
| `scenes/stairwell/stairwell.blend` | 656,966 | `8f4382edd9de07a801bc29768e9108c0f3bb61243404d6b96d4b1761696ae9f9` |

Next published `hero-v4` with a green outfit base colour at the normal working
path. With `hero-v3` selected for editing, a read-only Blender check inspected
the paths in `bpy.data.libraries`, resolved them and hashed the actual files.
It found one mismatch: working Hero SHA-256
`0fbeaa210b0dc1d860fecadad12b9f7c4bec05bd0ef9361b0164a11cf957d98b`
against the sealed v3 hash above. It stopped before editing. Working file bytes,
HEAD, index and clean Git status were unchanged. While v4 remained at the working
path, materialisation `1133c725-1415-4526-a6ff-b5abde1187de` hydrated all four
pinned rows exactly and Blender reopened that scene using the sealed v3 and
locomotion bytes. No working file overwrite, Git reset or snapshot edit was used.
This verifies the documented pre-edit procedure, not an automatic bridge fence.

## Evaluated poses and deformation

Scene metres. The lowest `ToeBase` tail was evaluated in world space at each
sample. The four support heights are 0, 0.17, 0.34 and 0.51 m. Each sampled toe
was 0.025 m above its support, and the lowest evaluated body vertex within 0.10 m
of that toe in XY was 0.0160–0.0210 m above support. This checks sampled
near-contact rather than a mathematically planted sole. Outfit deltas remove
hip translation and compare corresponding evaluated vertices against the previous
frame. Each body retained 10,086 vertices and each outfit retained 8,426.

| Frame | A action | A toe / nearby mesh Z (m) | A knee X (rad) | A outfit RMS / max (m) | B action | B support / toe / nearby mesh Z (m) | B knee X (rad) | B outfit RMS / max (m) |
| ---: | --- | --- | ---: | --- | --- | --- | ---: | --- |
| 1 | Walk | 0.025 / 0.0179 | −0.1600 | — | StairsUp | 0.17 / 0.195 / 0.1910 | −0.4100 | — |
| 2 | Walk | 0.025 / 0.0160 | 0.1040 | 0.2115 / 0.4501 | StairsUp | 0.34 / 0.365 / 0.3569 | 0.1722 | 0.3217 / 0.7594 |
| 3 | Walk | 0.025 / 0.0173 | 0.1600 | 0.0454 / 0.0967 | StairsUp | 0.34 / 0.365 / 0.3585 | −0.2296 | 0.2269 / 0.5355 |
| 4 | Run | 0.025 / 0.0205 | −0.5200 | 0.4118 / 0.9151 | StairsUp | 0.51 / 0.535 / 0.5297 | 0.4100 | 0.3503 / 0.8215 |
| 5 | Run | 0.025 / 0.0197 | 0.3380 | 0.4681 / 1.0482 | StairsDown | 0.34 / 0.365 / 0.3598 | −0.2900 | 0.3834 / 0.8871 |
| 6 | Run | 0.025 / 0.0193 | 0.5200 | 0.1058 / 0.2445 | StairsDown | 0.17 / 0.195 / 0.1888 | 0.2900 | 0.3287 / 0.7515 |

Instance B's low-toe X positions followed tread centres at 1.00, 1.22, 1.22,
1.44, 1.22 and 1.00 m. Instance A advanced along the landing. The outfit's
world Z bounds across the six frames were 0.1227–1.5961 m for A and
0.3196–2.0121 m for B. These are weighted mesh results, not cloth simulation.

## Admitted previews

| Purpose | Job ID | Attempt ID | Sealed snapshot | Native wall / peak RSS |
| --- | --- | --- | --- | --- |
| `proxy`, frame 2 | `preview-9d658ce4abf5c32922147f6c646eff62b46db66eff961d25d1e7e57050b9c11b` | `attempt-a6d74676-3e40-4b52-98c5-74920bf2f1e2` | `2de3c347-e3fe-426b-9174-606ae406cb15` | 1.81 s / 390,004 KiB |
| `draft_motion`, frames 1–6 | `preview-17404d135f36c531b86079291d9bc7b1bdeb746c1d26d9024fbe2f3e93993280` | `attempt-2f9af6e2-ad12-421d-896e-fe14ee30f092` | `faf785f7-5b54-44c4-b48d-669fb0a85d95` | 9.37 s / 794,448 KiB |

Both attempts exited `0`, did not time out and reconciled as `succeeded`.
The GLB was 3,966,652 bytes, SHA-256
`530ab7934da836d4483603583569e246e59dc1f01036b6122dae1f7510228659`.
Registered artefact IDs were the proxy job ID above followed by `-proxy` and
`-proxy-map`. The map's SHA-256 was
`e1c5a703900024f9f76dd4513e732c09c9d6a12d0731bbc50ef2ec9b7fae091a`.
It bound exactly the sample frame, scene version, proxy artefact ID and closure
digest, with eight mapped mesh nodes and eight unique instance IDs:

| Object ID | Instance IDs |
| --- | --- |
| `hero-body` | `inst-hero-a-body`, `inst-hero-b-body` |
| `hero-outfit` | `inst-hero-a-outfit`, `inst-hero-b-outfit` |
| `landing` | `inst-landing` |
| `tread-1`, `tread-2`, `tread-3` | `inst-tread-1`, `inst-tread-2`, `inst-tread-3` |

Motion: 640 × 360 RGB8 PNG, 24 fps, Cycles CPU, 8 samples, OpenImageDenoise on
CPU, AgX with `look: "None"`. Each receipt uses the motion attempt ID plus `-fN`.
Each frame artefact uses the motion job ID plus `-fN`. H.264 segment IDs use the
attempt ID plus `-s1-4` and `-s5-6`. All six PNGs decoded with finite, non-black
RGB8 values and distinct hashes. **0.0000%** of pixels matched the missing-texture
magenta predicate (`R ≥ 180`, `G ≤ 65`, `B ≥ 180`) in every frame. The camera
was static. Mean absolute RGB delta is against the immediately preceding sample
on a 0–255 channel scale.

| Frame | PNG SHA-256 | Native render (s) | RGB min / max / mean | Mean absolute delta |
| ---: | --- | ---: | --- | ---: |
| 1 | `ec9452bfef3e9a6dfa1323ac29ae6a014104c91cf1e35a035ea1c35375f8e99e` | 1.910 | 24 / 230 / 114.31 | — |
| 2 | `4ccdddccbdf2ab52833407acda1cb3aa7458af4d43104603af1f6f1272a1ae16` | 1.578 | 25 / 228 / 115.01 | 6.124 |
| 3 | `c68ac8c1393479faa90583325b30e4102278bbfca43b9c114deeb48d405937a0` | 1.293 | 27 / 226 / 115.04 | 5.556 |
| 4 | `c8039275990f4d03c41ca6c9ced506f771c40ad39ea84cd47d084841d19f8412` | 1.357 | 19 / 225 / 115.10 | 5.385 |
| 5 | `aeccc518ae4f9bb3b44f7235fe11f2d37c978b6484e4f25577adb83793ca5f57` | 1.293 | 25 / 225 / 115.03 | 5.672 |
| 6 | `e40c163d220327f80c01e22b255994b22167aa0978ff61db2cd8e503194d8006` | 1.277 | 20 / 226 / 114.63 | 4.621 |

An initial exploratory proxy attempt (`attempt-a22fe3cb-c35d-4067-9823-b87b1abb2f76`)
failed before export because its fixture supplied `look: ""`, which Blender rejects.
The corrected attempts used `"None"`. The successful proxy verified both override
instances with the existing worker. No worker change or override workaround was
needed. glTF emitted a sampler warning for a texture used by multiple nodes.
The check does not claim shader equivalence between GLB and Cycles.

## Resource measurements and limits

| Phase | Wall-clock time | Peak measurement |
| --- | ---: | ---: |
| Hero authoring | 2.07 s | 358,416 KiB RSS |
| Action authoring / standalone reopen | 0.38 / 0.32 s | 296,088 / 256,076 KiB RSS |
| Scene authoring | 0.35 s | 299,452 KiB RSS |
| Hero v4 colour revision | 0.28 s | 294,308 KiB RSS |
| Hero v3 / Locomotion v1 checkpoints | 0.274 / 0.158 s | 18,281,667 / 302,420 B Python peak |
| Composed checkpoint | 0.454 s | 2,151,275 B Python peak |
| Hero v4 checkpoint | 0.273 s | 18,242,019 B Python peak |
| Pre-edit refusal | 0.34 s | 311,968 KiB RSS |
| Verify and materialise 19,035,417 B scene closure | 0.110 s | 2,131,843 B Python peak |
| Independent sealed reopen and pose checks | 1.25 s | 374,808 KiB RSS |
| Native dependency and deformation instrumentation | 0.72 s | 328,996 KiB RSS |

The real worker's dependency function took 23.4–24.8 ms per sample with
17.33–17.38 MiB peak Python allocations, instrumented separately on the sealed
draft-motion input. It hashes the largest linked Blender file in memory. This
measurement complements Wave 4's maximum-bound source-path measurement. It is
not a latency target, disk-cold workload or native proof at the 512 MiB limit.
RSS and Python allocations are separate measurements.

Not verified: continuous stair contacts, slip or collision between samples,
visual motion quality, all internal floating-point render buffers, production
shader fidelity, streamed playback on a release browser, live bridge behaviour,
external Mixamo acquisition/retargeting, Unity animation or GPU rendering.
The fixture authored the Actions locally. No Adobe package, raw FBX, upload,
remote licence claim or owner creative approval was involved.

After cross-checking the evidence, the harness restored write permission on its
disposable `0o500` snapshot directories and removed the entire owned temporary
tree. No generated sources, renders, private configuration or databases are retained.

## Branch verification

- `make check` passed: 2,184 Python tests passed, 1 skipped, 220 frontend unit
  tests passed, and lint, type and generated-binding checks passed.
- Focused Chromium DPR 1 relationships and storyboard first-open suites: 21 passed.
- Offline skill, routing and worker capability suites: 142 passed. Checked 68 local
  Markdown links and 25 historical source anchors across 11 docs, with no errors.
- `make test-browser`: 180 passed, 3 failed. The failures match the earlier
  baseline reproduction: retained FLAC playback interrupted by `pause()`, send
  mode and Resume polling timed out, and Resume visibility while scrolling timed
  out. The full browser gate is not green.
