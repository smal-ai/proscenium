# Terminal readback reconciliation

The already-launched second readback completed with exit code 1. Its completion notification and durable final receipts have been consumed. **No readback is still pending. No new allocation, application stack, SDK send, provider request or model work was started during this reconciliation.**

## Actual result

- Actual app source: `4a41e99583c5556c2b5d28d9ce15af914d180d2c`, not the later parent-integrated `abedc19`.
- Actual browser: Chromium `159.0.8154.6`.
- Pairing succeeded. The real saved-project activation was attempted, but the proof guard rejected its read-only Git root validation before creating a child process.
- The run is **terminal failed before review**, with **zero verified views**. Displayed image URI/hash, selected job, known `shot-mug` and ContextSnapshot-ready target are **not verified by this run**.
- The guard's later exact read-only Git allowances and parent's subsequent test passes do not retroactively change this old run's outcome.

## Cleanup and evidence verification

`reconciliation.json` records byte-for-byte equality between the private durable receipts and their committed exports, verifies every file in the existing historical manifest and checks the recorded owned PID/start identities are no longer live. No process is signalled or restarted.

The durable shutdown receipt records server exit 0, browser-driver exit 1, both direct children reaped, port released, closed execution gate, no lifecycle errors, zero SDK/provider sends, unchanged original inventory and unchanged protected imported project/budget hashes. The attempted read-only Git command was rejected before child creation. A later private continuation file was not executed and did not resume the finished run.

All historical selector failures, screenshots, manifests and backup-sidecar records remain unchanged. The earlier red M07 PNGs were not altered, and the requested cobalt-blue correction remains unachieved. Parent retains milestone acceptance.

Reproduce this **read-only terminal reconciliation**, not the application run:

```bash
uv run python tests/native/studio/m07_review_reconcile.py /home/quintin/.cache/proscenium-development/m07-review-final/actual-b
```
