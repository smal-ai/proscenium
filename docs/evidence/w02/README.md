# W02 execution evidence

Evidence is recorded separately for offline API/storage checks and explicitly allocated
native source inspection. No fixture or offline result constitutes rendering, model,
GPU, installed-browser or cumulative product acceptance.

## Identity

- Date: 14 November 2026, SAST.
- Accepted shared baseline: `027327a129a3807ee100306bea99ad662d544240`.
- Worktree: `/tmp/opencode/proscenium-fleet/w02`, branch `impl/w02`.
- Implementation commits: `4758504` (sources/checkpoints), `9197e13`
  (projects/catalogue/API/tests) and `2278cc3` (consistent-backup lock regression).
- Contract 1.0, specification 3.2.0. No contract or migration changes.
- CPython 3.12.13, SQLite 3.53.1, Pillow 12.3.0.
- Git 2.53.0 and Git LFS 3.8.0.
- Local environments installed with `uv sync --frozen` and `npm ci --ignore-scripts`.
  The latter completed with zero reported vulnerabilities. No service installation,
  model load or browser binary installation was performed.

## Final offline checks

| Exact command | Observed result |
| --- | --- |
| `uv run pytest -q tests/integration/sources` | **52 passed**. |
| `uv run pytest -q tests/unit tests/contracts tests/integration -m 'not native' --ignore=tests/integration/native` | **145 passed**, including W02 and the accepted foundation regressions. |
| `uv run mypy apps/backend/src` | Passed, 36 source files. |
| `uv run ruff check apps/backend/src tests scripts tools/source` | All checks passed. |
| `uv run ruff format --check apps/backend/src tests scripts tools/source` | 60 files already formatted. |
| `make spec-check` | Passed document/schema/ownership/semantic-oracle checks and deterministic Python/TypeScript bindings. |

Pytest reports the existing Starlette TestClient/httpx deprecation warning. It did
not prevent execution. Browser/model/native suites are not smuggled into the offline
count.

### Assertions exercised

| Case | W02 evidence and remaining boundary |
| --- | --- |
| C06 | Actual production resolver opens historical and current linked Blender sources with different asset geometry and texture hashes. Independent copied snapshots retain old bytes while the working source changes. Conflicting destinations are rejected. Rendering remains W03. |
| C07 | Actual local Git LFS filtering/hydration, deterministic missing-object failure without fetch, explicit retained-source authority and missing-retained failure. Native reopening rejects an undeclared texture. |
| C08 | Concurrent expected-head publication race, stale head rejection, duplicate/recycled IDs, unchanged old metadata and one admitted render intent on replay. |
| C09 | Publication, versions, heads, initial jobs, attempts, intents and events roll back together on a fault after writes. A successful replay after gate closure retains its original job. Surviving-worker reconciliation is W03's integration responsibility. |
| C14 | Unsafe, secret/runtime, traversal and symlink source/staging paths are rejected. Internal publication rejects the owner channel. The combined suite reruns foundation token/origin/CSRF isolation. Native SDK scope remains W01/E01. |
| C15 | Create/open, private settings and CAS, settled move/rebind, consistent snapshot, duplicate-location rejection, no native work on open. Full native-history backup/restore remains W10 plus the explicit native relocation verifier. |
| C22 | Historical kind/name/description/tags/provenance/ancestry/Unity metadata and preview specifications survive database-backed reads. Synthetic Unity metadata explicitly uses `not_run`. Preview targets retain their pinned source version after head movement. No Unity execution is claimed. |
| C28 | Temporary index preserves unrelated staging byte-for-byte, unborn and existing-HEAD checkpoints, real LFS clean filtering, exact commit recovery after the Git-ref/SQLite ACK window and a later HEAD advance, conflicting request reuse, and fenced uncertain ref effects. |
| C30 | Exact storyboard-version/shot/frame plus choice resolution, different image/plan rejection, no source changes during reads, item selection projection and untrusted authored assembly-owner labels. Selection write/CAS authority remains W05. |
| C31 | Canonical 8-bit greyscale PNG, exact binary/soft coverage bytes and dimensions, rejection of coloured/alpha/ambiguous masks, and explicit derivative metadata. Provider polarity conversion remains W07. |

The route ownership test checks the exact set of all **17** normative W02 HTTP
operation IDs. API checks cover project lifecycle/settings/snapshot, catalogue and
typed source reads, image ingestion, metadata, GET/HEAD byte ranges and internal
publication. SDK checkpoint and publication bindings call the same service methods.

## Actual native source check

Final command:

```bash
PROSCENIUM_W02_NATIVE_FIXTURE=/tmp/opencode/proscenium-f01-20261114-a/project PROSCENIUM_W02_EVIDENCE=/tmp/opencode/proscenium-fleet/w02/docs/evidence/w02/native.json uv run pytest -q tests/native/sources --basetemp=/tmp/opencode/proscenium-w02-native-20261114-c --tb=short
```

**Observed: 1 passed.** The opt-in test copies the disposable F01 repository. It
never edits the original fixture or a reference repository.

The [native receipt](native.json) records:

- Blender **4.5.14 LTS**, build `62c1db4208e8`, actual executable path and zero exit
  codes for the two successful source inspections.
- Four CPU threads and a maximum 180-second timeout per process. Inspections run
  sequentially. There is no render, GPU, model, cloud or desktop-session operation.
- Exact historical/current fixture commit IDs and hydrated source-file hashes.
- Historical cube heights **1.0 m**, current heights **1.5 m**. The unchanged scene
  opens against different pinned asset/texture bytes in the separate snapshots.
- Linked library and external texture paths discovered by Blender inside each
  production materialisation.
- `dependency_missing` when the declared closure deliberately omits the texture.
- SHA-256 identities for the actual source resolver/inspection implementation.

The final source hashes in the receipt identify the tested bytes independently of
the later evidence-document commit. A pre-existing fixture alone is not cited as
the production resolver proof.

## Parent integration requirements

See [W02 implementation](../../implementation/w02.md) for all callable signatures,
namespaces and composition instructions. In particular:

1. Install the feature services and both routers in the parent-owned application.
2. W01 supplies the original admitted invocation association and preserves it on
   retries. It reads private project settings when freezing future requests.
3. W03 consumes `preview.render`, shares `preview_intents`, uses the F00 frozen
   job inputs and calls native dependency inspection before rendering. Worker
   media promotion/recording stays inside its existing receipt transaction.
4. W05 owns typed selection writes and uses canonical mask validation.
5. Coordinate other packages' owner mutations with `Services.activation_lock`
   during copy/rebind. Native relocation requires verified native history, not a
   guessed session ID.

Registry activation/event recovery is an explicitly documented cross-store saga,
not an invented cross-database atomic transaction. Unresolved checkpoint effects
remain fenced. There is no automatic fetch, destructive repair or media cleanup.
