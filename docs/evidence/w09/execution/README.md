# W09 execution extension and immediate resource blocker

**Implementation:** `8c2a0f72b5de2d86781f719ac01ca17406c2491c`, based on
`b9079722800613513fa31bf17649f643e0e70411`. **E04 and M10 remain unproven.**

## Actual allocation result

At **15:36 SAST on 14 November 2026**, `e04-live-a` observed:

| Check | Actual observation |
| --- | --- |
| Host `MemAvailable` | `11232055296` bytes, approximately **10.46 GiB**. |
| Admission fit floor | `12884901888` bytes, **12 GiB**. |
| Shortfall | `1652846592` bytes, approximately **1.54 GiB**. |
| Protected host headroom | Four GiB. Twelve-GiB fit floor also allows approximately 4.59 GiB for the checkpoint and 3.41 GiB for runtime/decoder work. This is a conservative test-allocation fit guard, not a measured model peak. |
| Independent ComfyUI queue | Empty. |
| Independent ComfyUI GPU free | `37585158144` bytes. This does not substitute for host RAM on this workstation. |
| Own server / job / reservation | **None created.** |
| Inference / Blender / checkpoint / publication | **None executed.** |

The harness refused immediately before weight rehashing, disposable-project bootstrap,
job admission or process creation. It did not wait for RAM, lower a threshold, unload
models, clear a queue, touch Blender port 9876 or mutate an independent service. This is
not a failed ROCm inference and does not establish that the candidate fails on this host.
It establishes a pre-admission resource blocker only.

The owned run directory contains only `report.json`. There is no prompt/client ID or
pending job to resume. Keep that directory intact. Any later execution needs a renewed
coordinator allocation and a fresh explicitly named run directory, not deletion/reuse of
`e04-live-a` or a silently lowered fit floor.

## Retained identities

- `native-resource-blocker.json` is an exact byte copy of the retained report at
  `/home/quintin/.cache/proscenium-development/image-to-3d/runs/e04-live-a/report.json`.
- `native-result.json` records its SHA-256, exact resource deficit and zero-effect result.
- Original shell: `sh_1da468bfb001mWtSW4c6hxLMZI`, completed before native startup.
- The report pins the implementation commit, installed Comfy revision, unexecuted argv,
  public endpoint observations and exact native-harness SHA-256.
- `code-verification.json` records 109 passing implementation tests before native harness
  addition. The final affected suite has 111 passing tests. Two additional tests verify
  actual W02 project/profile/checkpoint/publication calls with an explicitly fake Blender
  process and synthetic mesh. They are interface checks, not E04 native evidence.

## Bounded harness

`tests/native/image_to_3d/proof.py` is an explicitly authorised native-test entry point,
not an unbudgeted product CLI. It calls production `ProviderService` with one frozen
allowance/task/target and the accepted W02 exact-worker bridge. Its synthetic admission
context does not claim a Copilot SDK turn. The image is a labelled synthetic 2D red mug.
Actual mesh bytes, an actual bounded Blender process, source checkpoint and publication
would all be required before the hardware proof could be considered complete.

The allocation includes twelve-GiB host fit admission, ten-GiB disk reserve plus four-GiB
run allowance, a five-GiB stop threshold protecting four-GiB host headroom, one own server
up to 1800 seconds, four CPU threads, and one Blender import up to 180 seconds. Only the
exact owned PID/start-tick/process group is eligible for termination. Existing services
and user authoring instances are never lifecycle targets.

The read-only receipt test can verify the retained negative evidence without native work:

```bash
PROSCENIUM_W09_RESOURCE_RECEIPT=docs/evidence/w09/execution/native-resource-blocker.json \
  .venv/bin/python -m pytest -q tests/native/image_to_3d/test_resource_receipt.py
```
