# W09 preparation evidence

**Preparation only. E04 and M10 have not passed. No GPU inference or Blender startup.**

- `preparation.json`: exact application/runtime/template/model identities, source hashes,
  public availability and allocation. `gated=false` came from the official metadata API.
- `official-template.json`: unmodified official Comfy workflow, pinned source commit and
  SHA-256 in the manifest. Used as the graph-topology oracle, not as inference evidence.
- `model-card.txt`: original official model-card bytes, retained as text rather than
  treating upstream relative Markdown links as local Proscenium documentation.
- `model-licence.txt`: complete original official `LICENSE`. The card's `LICENSE.txt`
  link returned 404. Actual repository file and hash are recorded, not invented rights.
- `object-info-redacted.json`: actual installed schemas for selected native nodes.
  Unrelated `LoadImage` filename choices have been removed. The checkpoint list is
  genuinely empty and has not been replaced by a synthetic success catalogue.
- `system-stats.json`: actual read-only ComfyUI `/system_stats` response. Memory values
  are a point-in-time observation of an independent service, not an exclusive allocation,
  model-fit result or peak measurement.
- `prepared-api-graph.json` and `graph-identity.json`: exact prepared placeholder graph
  and SHA-256. These bytes were not submitted to a provider.
- `instance-plan.json`: an inspected, unexecuted temporary-instance plan. Port availability
  is historical and must be checked again at the actual allocation boundary.
- `generation-request.example.json`: explicitly synthetic normative `GenerationSubmit`
  example. Artefact IDs do not assert existing registered artefacts. A test validates the
  schema and proves W07 still returns `capability_unavailable` without creating a job/budget.

## Weight acquisition

**Completed and verified at 14:59 SAST on 14 November 2026.** See
`verified-weights.json` for the final absolute path, full-file SHA-256 and CPU-only tensor
directory inspection. The checkpoint contains 652 model, 727 conditioner and 266 VAE
tensors, with all expected DiT/vision/VAE prefixes. No tensor loading occurred.

The range downloader requested `4928151562` bytes, with no failed ranges or retries.
Including the separate eight-byte discovery header request, weight transfer requests
totalled `4928151570` bytes. The cap was `6442450944` bytes. Observed free disk after
verification was `361782738944` bytes, above the ten-GiB reserve.

The explicit preparation allocation permits a maximum of six GiB of one candidate's
weights. Only the pinned Hunyuan3D 2.0 single-file candidate is acquired. The downloader
uses the owned disk-backed model cache, a ten-GiB free-space reserve, a file lock,
64-MiB bounded public HTTP Range requests, durable conservative request accounting and
size/SHA-256 verification before renaming a partial file into its final name.

There are no automatic network retries. Re-running verifies an already completed file
or resumes a tracked partial. Failed/lost requests retain their byte charge. If the cap
would be exceeded, the helper refuses and retains evidence for the coordinator.
Mismatched bytes are not silently replaced or loaded. Model files, partials and journals
remain outside Git and outside tmpfs.

```bash
python3 -m tools.image_to_3d download-weights
python3 -m tools.image_to_3d inspect-weights
```

## Offline verification

Run from the W09 worktree using its frozen local environment. The source package path is
explicitly pinned to this worktree:

```bash
PYTHONPATH=apps/backend/src:. .venv/bin/python \
  -m pytest -q tests/integration/image_to_3d/
.venv/bin/python -m ruff check \
  tools/image_to_3d/ tests/integration/image_to_3d/
.venv/bin/python -m ruff format --check \
  tools/image_to_3d/ tests/integration/image_to_3d/
.venv/bin/python -m mypy tools/image_to_3d/
.venv/bin/python docs/validate_spec.py
git diff --check -- tools/image_to_3d/ agent/skills/image-to-3d/ \
  tests/integration/image_to_3d/ tests/native/image_to_3d/ docs/implementation/w09.md
```

The suite uses synthetic geometry and explicitly synthetic populated checkpoint choices
for positive schema tests. Negative tests use the actual empty-checkpoint catalogue.
See the implementation handover for the precise separate execution allocation needed next.

`make spec-check` ran the documentation/schema validator successfully, then failed in the
shared TypeScript generated-binding check because this isolated worktree has no
`json-schema-to-typescript` package installed. No shared lockfile or generated binding was
changed to work around that environment prerequisite. The command created only the ignored
local `.venv` from the existing frozen lock. Initial offline checks used the already installed
parent development environment with an explicit W09 `PYTHONPATH`.

The upstream model-card/licence snapshots retain their exact original bytes, including
upstream trailing spaces. They are source evidence, not reformatted maintained Markdown.

Final scoped results are recorded in `verification.json`: 37 W09 tests and 57 existing
provider tests, 94 combined, with one pre-existing Starlette/httpx deprecation warning.
Ruff lint/format, mypy on seven source files and the documentation validator passed.
No native/model, GPU, Blender, publication or cumulative product acceptance is claimed.
