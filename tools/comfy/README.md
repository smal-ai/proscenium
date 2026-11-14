# Budgeted ComfyUI adapter

Product execution lives in `proscenium.providers`, not a shell runner with a second ledger.
See [W07 integration](../../docs/implementation/w07.md) for binding, configuration and recovery.

These commands construct or inspect graphs only:

```bash
uv run python tools/comfy/graph.py --unet '<installed model>' --clip '<installed encoder>' \
  --vae '<installed VAE>' --width 1024 --height 576 --reference-artifact-id '<project image ID>'
uv run python tools/comfy/check.py --endpoint '<configured service root>' \
  --graph '<API graph path>' --model '<installed model>' --model '<installed encoder>' \
  --model '<installed VAE>'
```

Register graph/prompt/conditioning media through the accepted artefact service and submit
`GenerationSubmit` through `extensions['providers']`. Poll or reconcile that admitted job.
Do not run a fresh inference to recover a missing acknowledgement.

- No default endpoint, service installer, autostart, model download or global queue clearing.
- Seed/prompt tokens are bound by the host. An actual reference image is uploaded and
  connected through VAEEncode/ReferenceLatent, not mentioned only by filename in text.
- A graph is preflighted against the actual configured service before inference. A live
  schema match is not proof of GPU execution, model fit or visual quality.
- Reference provenance and changes are documented in [NOTICE](NOTICE). The retained
  upstream licence is [Apache-2.0](LICENSE). No reference project is a runtime dependency.

## Development-only bounded proof

`uv run python tools/comfy/prove_generation.py --execute` uses the real F00/W02/W05
composition and provider ledger/bridge in the specifically authorised home scratch.
It is not a general submit CLI. Stable IDs permit only the initial job and one explicitly
requested correction after an actual known initial error. Queue, service memory and host
MemAvailable checks can block without inference. The original 600-second deadline and
evidence survive accidental reruns. This allocation expired without inference because host
memory was insufficient. See the [measured result](../../docs/evidence/w07/integration-20261114.md).
