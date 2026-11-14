# Passive native denial and unentered checkpoint recovery

**Metadata recovery passed. No original authoring or publication continuation ran.**
14 November 2026, SAST. Base `2f1281e`, branch `impl/native-denial-recovery`.

Separate tested code commits:

- `ea4a1b7`: observe the exact departed native commands-gate denial.
- `4a6c4f3`: classify registered custom tools by validated operation, not prose.
- `f571413`: interrupt a proven-departed unentered checkpoint permission wait.

The [implementation handover](../../implementation/native-denial-recovery.md)
records the pinned CLI/embedded archive/native-core hashes, semantics, precise
guards, regression inputs and scope. No generic Bash-terminal inference, relaxed
permission-completion matching, schema/UI/job/lifecycle/native-authoring change,
private-binding repair or development child was introduced.

## M10: native commands denial, not an owner decision

The accepted original diagnostic and inspected candidate
`7d21240709737144e63f9b6c6f007207fda8b376` supplied the exact recovery case.
Source events for `call_Yo8Pyi1HvNYCI5E6CSQxCamx` are:

| Observation | Native event ID |
| --- | --- |
| Bash wrapper starts | `dbfcd230-d244-4c69-8a2a-cfd39e1d10fc` |
| First path permission requested | `efa0bc52-3da5-4074-8f5c-a7e2bc94c7fa` |
| Path gate approved | `7f9a2af3-7669-4305-899d-d75a332be969` |
| Final commands permission requested | `01ec62fb-3aa3-49e0-ae42-f9b2720bb48d` |
| Final commands gate denied | `43d539c1-3a3a-4173-abc4-8e09db265c51` |

The final permission ID is `1fbd60bd-5e63-4553-af72-8b6d50648dc1`. Native result
`denied-interactively-by-user` came from the host correlation failure, **not an
owner answer**. The exact raw command and parsed single segment agree with the
original invocation. There is no later approved/pending stage or contradictory
execution outcome. Raw history is required because the typed SDK projection loses
two relevant shell fields. Generic missing-result Bash calls do not qualify.

The metadata-only cold resume proved the previous recorded process gone and
native pending empty. The invocation became **failed on native authorisation
denial**, with a system observation referencing the original source IDs. This is
not a fabricated ToolResult, native exit code or successful operation. Live
invocations went **1 → 0**. Original work
`work-bc19a5da5fcd4aaf9ae552a13d06b20d` remains awaiting Resume.

Crucially, the orphaned permission stage is unchanged:

```text
phase: outcome_uncertain
reply: null
authority: null
delivery_state: not_sent
```

Its terminal native authorisation observation is stored separately from callback
delivery. Fresh native permission IDs are independent, with no inherited approval.
The written `assets/e04/revise_asset.py` was not executed by this proof, no v2 file
was created and no inference/provider/Blender process was started. W09/parent owns
any later original-work continuation.

## M11: operation classifier and lost checkpoint wait

Inspected candidate `7855b46` supplied the actual `CheckpointRequest`, whose paths
include `unity/M11/Assets/Proscenium/tool-install.json`. That filename is data,
not an installation request. Known registered custom operations now validate their
existing normative/native envelopes and exact original invocation/argument hash.
The permission grants entry only; the actual source/path/job/budget/caller guards
remain responsible for effects. Unknown tools still wait for an owner decision.
Real shell installation, sudo, push and upload confirmations remain in place.

The retained wait has additional real native evidence:

- Tool start `a9c0ab74-2a20-4b96-98e1-922514ce7555`.
- Permission request `ca6ff698-1d3a-448f-b284-55afea447991` for
  `cc62c65b-eb3b-4f3d-93a4-740a0925207f`.
- Native completion `d35f1be6-dd38-4b29-9bf4-aea761be9004` denied that gate after
  shutdown prepared a **host rejection**.

Cold metadata recovery verified the exact departed session/process, pending empty,
original bindings, matching native checkpoint arguments, unchanged expected Git
HEAD and absence of checkpoint intent/operation. It cancelled the unentered
checkpoint wrapper and the lost permission wait, with system evidence. Live
invocations went **1 → 0**. Work remains awaiting Resume, not completed.

Decision `decision-35e4534552dc528a97a1ae5035496f83af51d8cc430f11ae95cdef3efebb90e3`
is now cancelled with **response null, resolved_by null, delivery_state not_sent**.
No owner answer, owner decline or decision delivery ACK was invented.

The separate SDK permission stage had an actual prepared host `reject`. Existing,
unchanged completion matching acknowledged that rejection from its real native
completion event. This is **not** acknowledgement of an owner answer. The report
retains before/after stage fields explicitly instead of hiding the distinction.

No checkpoint intent, source commit, Unity publication, native job, Editor, GUI or
provider was created. Git HEAD remains
`3297ae647200270ebdc102630e7cfbfce2f4bfd1`. The parent owns any original publication
continuation under a separate allocation.

## Preservation, checks and cleanup

Before each cold resume, exact private copies of the project DB, registry DB,
native process record and session-state files were hashed and verified. They stay
outside Git under
`/home/quintin/.cache/proscenium-development/native-denial-recovery-2f1281e`.
No authentication or raw private model prompt is exported here.

Both reports verify unchanged source/scene/meta bytes, old top-level reports,
Git refs, original binding rows, contexts, immutable request submissions, original
work states and source/job/counter records. Native history prefixes and user-message
counts are preserved. No new send or continuation record was created. Original E04
inference was not invoked or modified. All recorded metadata SDK processes and
their parent hosts are closed, with exact PID/start identities retained.

- Denial handover: **51 focused checks passed**.
- Classifier handover: **34 focused checks passed**, including the actual M11
  arguments and a real checkpoint-handler `.env` rejection.
- Final checkpoint-wait recovery: **54 focused checks passed**. Production Ruff
  and mypy are clean. No broad model/native test loop was run.
- Six retained-evidence integrity/oracle checks verify these exports only.

`m10.json`, `m11.json` contain redacted actual metadata results and system events.
`manifest.json` hashes the exported evidence. The driver enforces one pass per
fresh named metadata directory and blocks all model sends/tool execution.

```bash
PYTHONPATH=. uv run --offline python tests/native/copilot/native_denial_metadata.py report
uv run --offline pytest tests/acceptance/native_denial_recovery_test.py -q
```

Do not rerun reconciliation/Continue or the written revision script without a new
instruction. Metadata settlement does not certify M10/M11 product completion.
