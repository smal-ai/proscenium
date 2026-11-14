# E04 bounded execution allocation

The active allocation is exactly **`e04-live-b`**, authorised after acceptance `9720285`.
`e04-live-a` remains immutable report-only zero-effect evidence and is not accepted by the
current execution CLI. The provider implementation is accepted. Offline tests do not
establish native acceptance.

```bash
PYTHONPATH=apps/backend/src:. .venv/bin/python -u \
  tests/native/image_to_3d/proof.py --allocation e04-live-b
```

This command owns one fresh run directory and refuses to repeat an existing run. It is not
a general-purpose directory selector. The exact allowed root is
`/home/quintin/.cache/proscenium-development/image-to-3d/runs/e04-live-b`. It is not
a general-purpose inference CLI. It calls the production `ProviderService` exactly as a
trusted test caller, using a labelled synthetic 2D reference and trusted harness admission
context. No actual Copilot SDK turn is claimed. W01's frozen allowance record is one, with
one stable generation task/target and one original invocation. Real W02 project/profile,
artefact bridge, checkpoint/publication, F00 resources and provider transport are used.

The fixed native allocation is one owned ComfyUI process, maximum 1800 seconds, four CPU
threads, official graph/seed and checkpoint, then one background Blender import/save of at
most 180 seconds. The fit floor is twelve GiB of available host RAM. The sampler stops only
its exact owned process group below five GiB to protect four GiB of headroom. It never
unloads models, clears queues, stops another process, contacts Blender port 9876 or renders.

Full report/logs/model outputs/database stay in the disk-backed owned run directory.
Once an inference has uncertain acceptance, only the original client/prompt is observed.
Never rerun the script, reset its state or delete its directory to obtain another attempt.
See `docs/implementation/w09.md` for the authorised boundary and remaining acceptance.
