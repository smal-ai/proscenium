# Budgeted ComfyUI adapter

Product execution lives in `proscenium.providers`, not a shell runner with a second ledger.
See the [operator guide](../../docs/operator.md) for configuration and recovery.

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

Development-only model preparation and proof scripts are retained on `impl/studio`.
They are not required by the application or its configured generation adapter.
