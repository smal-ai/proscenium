# Native source fixture

Creates the F01 floor, wall and two instances of one linked, textured cube in an explicitly disposable directory. The second asset commit changes both mesh height and texture. A separate historical tree hydrates the original Git LFS bytes and is reopened by Blender for measured bounds and dependency inspection.

```bash
python3 tools/proofs/blender/create_fixture.py --output /tmp/opencode/proscenium-f01
```

The generator uses one background Blender process at a time, four CPU threads, no GPU rendering and a 180-second per-process limit. It disables embedded auto-execution and does not load the desktop author's scene or preferences. Re-running a completed fixture verifies its immutable historical file hashes and returns its receipt. A partial output is retained and cannot be overwritten by a blind retry.

The receipt proves actual fixture generation and historical reopening. Production source-resolver, publication, rendering and Copilot acceptance must additionally exercise their implemented paths. Generated binaries and runtime logs remain outside source control.
