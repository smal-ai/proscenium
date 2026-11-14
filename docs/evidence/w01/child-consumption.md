# Native child consumption follow-up

**14 November 2026, SAST. Passed on the first cloud attempt.**

Base: `d068ed9f0433723e52253a8bd4b19a5bb1a1dee7`, branch `impl/w01-child`.
This closes the bounded **native child structured-selection consumption and real
tool callback** proof. It does not claim the full M01 authoring journey or any new
Stop/restart/input/transport-loss fault acceptance.

## Actual useful operation

The host prepared an isolated disk-backed project through W02's real project,
checkpoint and publication services. The published asset is explicitly a synthetic
text fixture, **not an F01 Blender scene or rendered media**. A real F00 JobRecord
targets that published item/version and stays `planned`, revision zero. It has no
launch intent, no worker process and no completed output. Its kind is `unity`, but
Unity was never invoked. The fixture job has an owner-operation association and
`work_id: null`; reading it does not retag it to the native work.

The job ID equals the selected item's random opaque ID. Only the immutable captured
selection supplies that ID. The root is instructed to delegate one status read,
not reveal a system prompt or echo additionalContext. A pre-tool guard prevents
passing the opaque item/version/context IDs in delegation arguments. The specialist
has only `get_job` available, with model policy `required`.

The child called the **existing production `get_job` SDK tool**. A passive test
wrapper checked its actual ToolInvocation against the production `invocations`
and `copilot.payloads` records, awaited the original production handler, and
observed the real `JobService.get` reading SQLite. It committed the observation in
the same project database's test-only `copilot.child_consumption` namespace before
returning the **unchanged** ToolResult to the SDK. No recording tool, fake service
result or replacement agent loop was added.

The public child answer at event sequence 39 was:

> The selected job is **planned**, with availability **pending**. Exactly one read-only status check was performed.

The original job record, published source bytes and Git HEAD were unchanged after
the read. Both child and root completed. No native adapter correction was needed.
`native.py`'s existing wrapper explanation was exercised as-is. The production
`service.py` and `state.py` files were not edited.

## Exact trace

| Identity | Observed value |
| --- | --- |
| Project | `project-1cc068f6-efb7-448c-8b4d-abc99cb3a781` |
| Selected item = job | `item-27ce50ffb9bb4a1d89fedc4c8e5dd3e4` |
| Published version | `version-52d367f0d99b4046b5738a6c3c12fcd6` |
| Published source commit | `e9b3f78ef5a68b782e34ba5996e33669c83cad85` |
| Context | `child-read-context` |
| Request | `child-consumption-ce6763cc83bc4593bb7e2d0dc6092d84` |
| Work | `work-593d435168c94d58bef43243f3a67519` |
| Native root session | `b73c7578-8bc0-413d-99c2-b060deaeb99a` |
| Native child | `502153b9-999b-496e-9587-3865349ee7e1` |
| Native delegation tool call | `call_el7fr34IBMdMqjTuJMDwyDnQ` |
| Actual child get_job tool call | `call_SA4igW6JpoDvqRP02ICQlBtb` |
| Persisted tool invocation | `c9ac09976b2f697a127f07a1753f5f60962b315e15e1fd64120c71abc5a45ad2` |
| Model / selection source | `gpt-6.1-sol` / `configured_required` |
| Native tool invocation model/state | `gpt-6.1-sol` / `completed` |
| Hook payload SHA-256 | `7a606d90191214bef627d5dc632ac27db0750a2eac7e31b9ff5dcfc044690944` |
| Final stored job state | `planned`, availability `pending`, revision `0` |

The initial tool arguments were exactly:

```json
{"job_id":"item-27ce50ffb9bb4a1d89fedc4c8e5dd3e4"}
```

The persisted callback observation joins that argument to the exact project, root
work/request, child agent and captured item/version. The native hook is supporting
attribution evidence, **not** the sole consumption evidence.

## Allocation, runtime and cleanup

```bash
W01_CHILD_PROOF_ROOT=/home/quintin/.cache/proscenium-development/w01-child-proof-20261114-a \
  uv run pytest tests/native/copilot/production_child_consumption.py -q -s
```

Result: **1 passed**, one existing Starlette TestClient deprecation warning,
**39.18 seconds** for the complete invocation. The root wait was bounded to 119
seconds. Used **one cloud root and one executed native child**, against the separate
maximum of two each. No optional live Stop case was attempted. The durable ledger
is `/home/quintin/.cache/proscenium-development/w01-child-proof-allocation-068ed9.jsonl`.

- CPython 3.12.13, github-copilot-sdk 1.0.34, direct CLI 1.0.112.
- Explicitly authorised native binary:
  `/home/quintin/play/proscenium/node_modules/@github/copilot-linux-x64/copilot`.
- Binary SHA-256:
  `463978cdea4ad6973b615f1ba07b7837634218632480ef06307ea844ee369a69`.
- SDK uses the official existing login, token-free environment forwarding and the
  unchanged exec/parent-death launcher. No login/token extraction occurred.
- Owned native PID `3124803`, `/proc` start identity `45324935`, stopped/reaped.
  Its `/proc` entry disappeared before the harness finished.
- Native histories and the disposable database remain under the stated on-disk
  proof directory, outside Git. Only allowlisted observations are committed.
- No Blender, Unity, GPU, ComfyUI, LM Studio or independent service operation ran.
  The earlier fixture preflight used another isolated directory and zero model calls.

`child-consumption.json` pins the observed production-file hashes. The raw report
initially abbreviated the base as `068ed9`. The committed report records the full
verified base above. Post-run harness-only amendments expand that metadata label
and scope duplicate budget callbacks to their proof root, with an offline test.
No additional cloud run was used. The executed harness SHA-256 was
`59487e9b89da6a5dbcc95c01ce4ee52f3e20c8d94ffe2d272ace28f6e9afcac7`.

## Tool failures and authoring instructions

Tool failures retain the existing top-level `error` code and add `message` and
`details`. Only an exact allowlist of application-authored messages is forwarded.
Unknown messages, nested provider details, credentials, headers, paths and native
exception representations are not copied. Safe non-negative revision integers and
the public CPU preview recipe can be included. Unexpected service exceptions become
a static `internal_error` with a warning to inspect the original receipt before
retrying. Cancellation is not swallowed.

The Blender authoring skill now documents the accepted launcher, explicit active
project and source/checkpoint/ID semantics, `proscenium-blender-1` CPU/Cycles preview
recipe, and preservation of dirty/unknown sessions. No home-path defaults, add-on
installation or force-close instruction was introduced. These are instructions,
not a claim that Blender authoring was exercised by this child-read test.

Checks: **49 W01 integration tests passed**, including three new failure-redaction
tests, plus a targeted offline ledger-scope test. Targeted mypy and Ruff
checks/formatting pass. No broad native retest loop or second cloud request was used.
