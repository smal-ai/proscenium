# M14 bounded SSH message/media and unavailable-service proof

## Allocation and preflight

Accepted product base `904f73383d6e9a3e0a5daa411f3628efd90bc2cf`, contract unchanged.
Only proof drivers, affected acceptance checks and this package's evidence are owned.
Cumulative M12/M14 and release acceptance remain coordinator-owned.

- One actual SDK Queue, GPT-6.1-Sol, at most 180 seconds. Only native `view` is
  exposed, with no custom tools or agents. An exclusive durable send reservation
  refuses a second send across process restart.
- One private fixture-key loopback sshd, at most two sequential fixed-forwarding
  clients, one owned API at a time and one continuous 1920 × 1080 Chromium profile.
  Whole allocated proof is bounded to 600 seconds, including cleanup.
- Four-GiB available RAM is checked once immediately before allocation. No waiting
  for resampling, shared-service lifecycle or native authoring/rendering is allowed.
- Build and dependency installation precede allocation. SDK `1.0.34` and the
  official direct CLI `1.0.112` are hash checked. Existing supported login is reused.
- Standalone imports, exclusive reservation, full-body transport hashing without
  auth retention and lifespan-before-termination audit persistence are checked offline.

## Import boundary

Fresh private run root: `/home/quintin/.cache/proscenium-development/s14a`.
The original completed M13 root `c13c` is read-only. Source Git/LFS is cloned without
hardlinks and original source/media/database/native-history bytes are hash inventoried.
Only selected v1 and its source version, catalogue, verified terminal job/frame history,
registered artefacts and the original explicit v1 selection are imported. No original
request, invocation, authentication, annotation or native session/history is copied.

This is an isolated **import of actual history**, not newly rendered media or a
continuation of M13's conversation. The immutable project/repository IDs are preserved
because actual source maps and delivery manifests bind those IDs. A fresh registry and
new native association are used. A single owned `m14-marker.txt` is the only new model
input file. The accepted original selected silent film is 10 frames, 640 × 360, 24 fps,
64,876 bytes, SHA-256 `ddf01466d4a086bcdae27890e6cbabaa2bf309b62e48bf9353a056d92a60aa8e`.

## Execution contract

```bash
uv sync --frozen --python 3.12
npm ci --ignore-scripts
npm run build
uv run pytest -q tests/acceptance/m14_ssh_offline_test.py tests/acceptance/test_ssh_proof_contract.py
uv run python tests/native/studio/m14_ssh_prepare.py /home/quintin/.cache/proscenium-development/s14a
uv run python -u tests/native/studio/m14_ssh_run.py /home/quintin/.cache/proscenium-development/s14a docs/evidence/m14-ssh/result.json
```

The prepare command already ran with zero SDK/SSH/browser allocations. Its source
import materialises the selected historical source through accepted W02 tools and
verifies exact ready media bytes. The live command must run once only. No retry of
the live allocation is implied by these instructions.

The unavailable restart uses documented `Settings.load()` configuration, a genuinely
missing direct native executable and unused closed loopback provider ports. Public
`profiles[].scope_status=unavailable` and `services[].available=false` are **configured
metadata**, not successful health probes or evidence of physically stopping shared
services. The driver separately observes TCP refusal at those private configured ports.

## Result

Pending the single allocated execution. No native/message/media acceptance is claimed
by the preflight or synthetic helper tests.
