# W08 evidence: 14 November 2026

**Latest input validation: passed.** The [two-invocation input follow-up](input-20261114/README.md)
closed the headless virtual-input defect using actual Keyboard/Mouse polling for every measured
control in both projections. M11 production acceptance remains with the parent.

## Preserved initial allocation: failed

The table, `manifest.json` and `native-20261114/` below are the original historical result, not the
latest rerun. The original failed XML, metrics, source hashes and logs have not been replaced.

| Check | Actual result |
| --- | --- |
| Blender source/export | Four native evaluated meshes, 32 vertices and 48 triangles. Exact copied historical source hashes and closure recorded. |
| Unity API import | Passed on the second actual Editor invocation. Scene saved, URP 17.3.0 active, finite metre-scale geometry and supported spawn. |
| Repeat import/GUIDs | Passed. Every listed mesh/material/pipeline/renderer/scene/manifest GUID retained. One marked root, unrelated sentinel retained. |
| Perspective PlayMode physics | Movement 3 m, floor error 0.0199999213 m, wall penetration 0 m, reset error 0 m. Injected look changed real camera rotation. |
| Orthographic PlayMode physics | Same measured criteria passed. Real CharacterController/PhysX in Editor PlayMode, simulated frame delta 1/60 s. |
| Virtual keyboard polling | Failed. Queued W generated 0 m movement. Suite stopped at that assertion, so virtual A/S/D/R and mouse-device events were not reached. |
| Physical OS input | Not tested. Injected controller/look/reset inputs are not a physical keyboard/mouse proof. |
| Native SDK/source publication | Not exercised by W08. Parent integration required. |

## Stored evidence

`native-20261114/` contains:

- Original admitted job/attempt/result records for all three actual Unity invocations.
- The one pre-launch stale-lock blocker and exact allocation counts.
- Actual PlayMode NUnit XML, measured controller/camera metrics and pre/post GUID pairs.
- SourceClosure/private recipe, actual Blender export bounds/IDs/materials/hash and resolved package lock.
- Exact executed Unity source/scene/asset hashes and the executed test source snapshot.
- Raw log path/size/hash identities. Raw Editor logs remain in the disposable fixture, not Git.

The fixture root is `/tmp/opencode/proscenium-unity-w08-20261114-a`. Blender was **4.5.14 LTS**
(`62c1db4208e8`, installed identity supplied by the allocation). Unity was the official
`/home/quintin/Unity/Hub/Editor/6000.3.27f1/Editor/Unity`. The runtime pipeline was
`UnityEngine.Rendering.Universal.UniversalRenderPipelineAsset`, not an assumed URP label.

The saved scene is
`unity/Fixture/Assets/Proscenium/Generated/713fc42da34ae8eb436b3c2d/Playable.unity` relative to
the fixture's active Proscenium root. Native C# receipts originally reported Unity's `Assets/...`
asset path. The final Python adapter qualifies catalogue paths beneath `unity/Fixture/`.

The historical F01 directory was copied before use. The proof supplies a labelled fixture
`SourceService` adapter with retained-origin hashes, not invented source Git commits or production
W02 records. Real F00 JobService admission/attempt/state/resource persistence used disposable SQLite.
The first template Input System version, 1.12.0, failed compilation against a removed Editor API.
The Editor-bundled 1.20.1 fixed that concrete failure. Its own stale lock was preserved separately
after verifying the failed native PID was gone. No second Editor started while another was live.

At the original handover, the test had gained explicit headless focus/device routing overrides and
additional assertions, but those changes had not yet had another Unity invocation. Python
installer/recovery/cancellation/path qualification also gained offline hardening. The original
executed test snapshot and failure remain unchanged. The separate input follow-up above now records
the actual rerun and stricter passing all-polling suite. Do not relabel the old failed XML as passed.

## Commands

```bash
PYTHONPATH=apps/backend/src:. python tests/native/unity/proof.py initialise \
  --root /tmp/opencode/proscenium-unity-w08-20261114-a \
  --historical /tmp/opencode/proscenium-f01-20261114-a/historical

PYTHONPATH=apps/backend/src:. python tests/native/unity/proof.py repeat-proof \
  --root /tmp/opencode/proscenium-unity-w08-20261114-a \
  --editor /home/quintin/Unity/Hub/Editor/6000.3.27f1/Editor/Unity

# Same action again after the evidenced package fix and owned stale-lock quarantine.
# One actual native invocation, not the earlier blocked admission.
PYTHONPATH=apps/backend/src:. python tests/native/unity/proof.py repeat-proof \
  --root /tmp/opencode/proscenium-unity-w08-20261114-a \
  --editor /home/quintin/Unity/Hub/Editor/6000.3.27f1/Editor/Unity

PYTHONPATH=apps/backend/src:. python tests/native/unity/proof.py playtest \
  --root /tmp/opencode/proscenium-unity-w08-20261114-a \
  --editor /home/quintin/Unity/Hub/Editor/6000.3.27f1/Editor/Unity
```

The reproduction harness refuses a fourth Unity invocation or reuse of an existing initialisation
root. A new proof requires a fresh explicitly authorised allocation/root. The native allocation was
one owned Editor at a time, at most 600 s per invocation, one Blender export at most 180 s/4 threads.

Offline checks use process/source doubles explicitly labelled in tests. They prove paths,
idempotence, pre-launch admission, timeout/reaping, uncertain-attempt fencing, source conflicts,
installer preservation and terminal claim-release recovery. They are not native PlayMode evidence.
