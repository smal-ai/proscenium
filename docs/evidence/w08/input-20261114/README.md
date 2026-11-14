# W08 input follow-up: passed

**Date:** 14 November 2026, SAST. **Accepted application base:** `0b0d87391e34685632dc56a63bcea636b38068d4`.
**Implementation:** `278d3f616892def8619d58c250275af6046e34f4`.

The included headless focus/device-routing correction passed on the first new invocation. The
second invocation passed a stricter suite with **every movement, look and reset input** sent through
virtual Input System events, `Keyboard.current`/`Mouse.current`, the controller's actual `Update`,
and the real CharacterController/PhysX/camera. The final suite does not call `Tick` or `Respawn`
directly and does not set `testMove`, `testLook` or `testReset`.

The original [failed allocation](../native-20261114/attempt-3-playmode.xml) remains unchanged.
This is native F05 input/collision acceptance, not native SDK delegation or full M11 completion.

## Actual measured final results

| Check | Perspective | Orthographic | Required |
| --- | --- | --- | --- |
| Forward W movement | 3 m | 3 m | >0.25 m |
| W/A/S/D movement, each direction | 0.666667 m | 0.666667 m | >0.25 m each |
| Floor support error | 0.0199999213 m | 0.0199999213 m | ≤0.05 m |
| Wall penetration | 0 m | 0 m | <0.02 m |
| Maximum R reset error | 0 m | 0 m | ≤0.01 m |
| Mouse camera rotation | 8.94275° | 8.94275° | >1° |
| Mouse yaw / pitch | 8° / 4° | 8° / 4° | Both change |

The final XML records **one actual PlayMode test passed, zero failed, zero skipped**. Input System
keyboard ID **3** and mouse ID **4** matched the devices observed inside the production controller's
polling branch. The controller recorded the expected WASD vectors, mouse delta `(80, -40)` and R
pressed state. `injectedInput` stayed false for the entire measured suite.

The Editor reported `Application.isFocused == false`. The test deliberately sets manual updates,
`IgnoreFocus`, `AllDeviceInputAlwaysGoesToGameView`, enables its own virtual devices and makes them
current. This corrects the missing Game-view focus/device routing in the earlier headless harness.
The first rerun retained detailed queued-versus-polled identity/state/frame/phase diagnostics.
It verifies the combined correction, not an isolated causal proof for one individual setting.

The final test's `finally` block removed its own virtual devices and asserted restoration of:

- `ProcessEventsInDynamicUpdate`.
- `ResetAndDisableNonBackgroundDevices`.
- `PointersAndKeyboardsRespectGameViewFocus`.
- Original capture delta, which was zero.

Production focus policy was not weakened. **Physical OS keyboard/mouse events were not tested.**

## Exact source and runtime identity

| Identity | Value |
| --- | --- |
| Official Editor | `/home/quintin/Unity/Hub/Editor/6000.3.27f1/Editor/Unity` |
| Actual Editor version | `6000.3.27f1` |
| Actual pipeline type | `UnityEngine.Rendering.Universal.UniversalRenderPipelineAsset` |
| Input System | `1.20.1` |
| URP / Core / Shader Graph | `17.3.0` |
| Test Framework | `1.6.0` |
| Controller SHA-256 | `ddad62009d7cf8979ca520f8fcc7b70b07c3d720d1f2aba3957526026ab39bfa` |
| Final test SHA-256 | `0616357329be3770268340f93331cb89758d7ca36f00b29653554576d6f7ed29` |
| Saved scene SHA-256 | `6d91581fadde98edf6b9f330297351e3dc1ff856fd524e268b26005cdd643b6e` |
| Scene GUID | `fa9b84079137819b3a554057bb248e60` |

The exact executed controller/test snapshots, source hashes and full resolved package lock are
preserved per invocation. Both runs left all **28 pre-existing `.meta` files byte-identical**,
including the original 12 generated mesh/material/pipeline/scene/manifest GUIDs. See
`attempt-2/guid-retention.json` for each actual GUID and pre/post metadata hashes.

The marked disposable fixture is on the home filesystem, not RAM-backed `/tmp`:

```text
/home/quintin/.cache/proscenium-development/unity-w08-input-20261114
```

The actual saved scene relative to that active Proscenium root is:

```text
unity/Fixture/Assets/Proscenium/Generated/713fc42da34ae8eb436b3c2d/Playable.unity
```

Only the old fixture's Assets/Packages/ProjectSettings, source bytes and export/recipe were copied.
No Library cache, owner database, owner lock or prior runtime process identity was copied. The old
`/tmp/opencode/proscenium-unity-w08-20261114-a` sources and failure logs remain untouched.

## Real W02 source dependency

The follow-up calls `proscenium.catalogue.install(services)`, then the accepted source checkpoint,
catalogue publication and `SourceService.materialise` methods. No source-service fake is installed.

- Source checkpoint: `4a6407c884f104abfad34c42026743218845234f` in the disposable fixture repository.
- Catalogue version: `fixture-source` / `fixture-source-v1` in project `project-w08-input`.
- Git-origin closure: `8cc1bd3a28c1eeaa3e105dc9f7be388303fef90700fa88225c7ff00da812cbeb`.
- Blender entry: `scenes/fixture/scene.blend`, SHA-256
  `17a4dda0efd2ef312d0b14d709d453de6cfb2c3986714eaee8042fd1a43dbe9f`.

`source-evidence.json` contains the real checkpoint/publication receipts and a sealed materialisation
receipt identity. `materialisation-receipts.json` lists all four actual W02 snapshots created by
publication, setup verification and the two Unity-job preflights.

The copied Blender sources match the original three hydrated hashes exactly. No Blender process
was needed. The existing mesh export's geometry values were reused unchanged, with only its copied
closure metadata rebound to the new Git-origin source closure. The already saved Unity scene and
its original import manifest were reused. This proves real W02 source preflight for the input rerun,
**not a fresh Blender export, Unity import or Unity-output publication**.

## Allocation and process evidence

- Exactly **two** new Editor invocations, sequential, each bounded at 600 seconds.
- Both jobs were admitted through real F00 JobService before launch and finished with native exit
  code **0**, terminal worker receipts and no remaining owned attempt processes.
- No timeout, forced process termination, unrelated process kill, new Blender invocation, Xvfb,
  cloud/model call, GPU rendering, Linux build or service configuration change occurred.
- Free space before the first invocation: **371,535,761,408 bytes**. After the second:
  **369,862,926,336 bytes**, above the **5,368,709,120-byte** reserve.
- Per-attempt request, worker identity, XML, metrics, UnityResult and raw log hashes/locations are
  retained. Raw Editor logs stay in the disposable root, not Git.

## Reproduction and offline checks

`tests/native/unity/input_proof.py` records the real setup/run/collection shape. Its allocation is
exhausted and refuses a third Editor invocation. A future rerun requires a newly authorised root
and allocation, not resetting this history.

```bash
PYTHONPATH=apps/backend/src:. python tests/native/unity/input_proof.py setup \
  --root /home/quintin/.cache/proscenium-development/unity-w08-input-20261114 \
  --previous /tmp/opencode/proscenium-unity-w08-20261114-a

# Executed twice, once for the included correction and once for the all-polling suite.
PYTHONPATH=apps/backend/src:. python tests/native/unity/input_proof.py run \
  --root /home/quintin/.cache/proscenium-development/unity-w08-input-20261114 \
  --editor /home/quintin/Unity/Hub/Editor/6000.3.27f1/Editor/Unity
```

Affected offline checks: **31 pytest tests passed**, Ruff and formatting passed, mypy passed.
The new offline tests check the 5 GiB reserve, disposable marker and two-invocation cap. They are
not substitutes for the stored real PlayMode XML. The parent still composes the SDK specialist and
owns cumulative application acceptance.
