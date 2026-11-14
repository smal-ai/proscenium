# Development and acceptance operations

## Historical native evidence at a later integration revision

Keep each receipt's `application_commit` at its **actual tested** full commit SHA.
Do not rewrite native/model evidence identities after unrelated integration or
documentation commits. `--application-commit` is the proposed integration revision.

A differing tested revision requires an explicit top-level `applicability` record
in the receipt document. The coordinator creates/reviews this after integration
validation. No equivalence is inferred from ancestry alone. Shape example only:

```json
{
  "applicability": [{
    "format": "proscenium-applicability-1",
    "receipt_sha256": "EXACT_CANONICAL_RECEIPT_SHA256",
    "tested_commit": "ORIGINAL_TESTED_FULL_COMMIT_SHA",
    "accepted_commit": "EXISTING_ACCEPTANCE_FULL_COMMIT_SHA",
    "integration_commit": "PROPOSED_INTEGRATION_FULL_COMMIT_SHA",
    "reviewer": "coordinator",
    "decision": "applicable",
    "reason": "Exact reviewed scope and why the recorded regression checks establish applicability",
    "mode": "reviewed_ancestor",
    "validation_artifacts": [{"path": "docs/evidence/actual-integration-regression.json", "sha256": "ACTUAL_REGRESSION_SHA256"}]
  }]
}
```

- `receipt_sha256` is produced by `tools.operations.applicability.receipt_digest`.
  It binds the entire original receipt, including status, evidence hashes and limits.
- All commit values must identify known **commit objects**, not branch aliases,
  unknown hashes or unrelated roots. The tested commit must precede the accepted
  commit, which must precede the target integration commit. Equality is allowed.
- The coordinator's explicit reason and hash-verified regression artefacts are
  mandatory. At least one validation artefact must differ from the original proof
  artefacts. This is applicability validation, **not a fresh native execution**.
- Alternatively set `mode: "source_hashes"` and add a non-empty `source_hashes`
  object mapping each reviewed relevant source path to its SHA-256. Those exact
  committed regular-file bytes must match at both tested and integration revisions.
  The coordinator still owns the file/dependency coverage and regression judgement.
  The validator reads Git objects, never other worktrees' uncommitted source.
- Unknown fields such as `limits_override` are rejected. Receipt limits/status are
  never changed. Changing merely `live_pass`, command or any receipt field invalidates
  the bound applicability record. Unproved native/browser/M10 requirements stay open.
- These are explicit review records, not signed identity attestations. The parent
  remains responsible for creating/reviewing them under its actual authority.

The generated report includes `validated_applicability`, separately from execution
observations. No release-ready receipts or applicability claims for unfinished
milestones have been populated by W10.

Use the frozen root locks and maintained tools from this checkout. See
[operator guidance](operator.md) for private stores, service ownership and recovery.
W10 changes no shared service, schema, lock or application composition.

## Bounded offline checks

Choose a **new** disk-backed pytest directory, not the memory-pressured `/tmp`.
Pytest owns its `--basetemp`, so never point it at owner data or reusable evidence.

```bash
uv sync --frozen --python 3.12
uv run ruff check tools/operations tests/acceptance
uv run ruff format --check tools/operations tests/acceptance
uv run pytest -q tests/acceptance \
  --basetemp "$HOME/.cache/proscenium-development/w10-pytest-unique" \
  --junitxml "$HOME/.cache/proscenium-development/w10-junit-unique.xml"
uv run python -m tools.operations.acceptance \
  --junit "$HOME/.cache/proscenium-development/w10-junit-unique.xml" \
  --output "$HOME/.cache/proscenium-development/w10-coverage-unique.json"
```

The tests compose real accepted F00 SQLite/ownership/gates, W02 Git LFS/catalogue/
artefact/retention services, W05 annotations/selections and W01 immutable admission/
quiet startup. They exercise missing/corrupt source/media, committed WAL backup,
native association byte restoration, record retries, traversal, symlinks, existing
targets, live ownership/process refusal and historical secret-path refusal.
**Native history bytes and SDK transport are explicitly synthetic.** No real
conversation inference, model, GPU, Blender, Unity or browser allocation is consumed.

Use the repository's existing `make spec-check`, lint/typecheck and specific feature
tests when integrating relevant changes. Do not rerun every native/browser workload
to produce a larger green number. Native suites require their own explicit allocation.
Do not edit other worktrees or adopt their uncommitted changes as accepted interfaces.

## C01–C31 orchestration

[`tests/acceptance/coverage.json`](../tests/acceptance/coverage.json) maps every named
scenario to existing concrete Python test functions and required evidence layers.
This is a **coverage map**, not a blanket claim that one function completes the
entire scenario. Browser/native gaps and fixture limits remain visible per scenario.
The validator resolves paths/functions using AST, without importing tests or launching
tools. A deleted/renamed test fails validation.

```bash
uv run python -m tools.operations.acceptance
uv run python -m tools.operations.acceptance --release
```

- Normal validation exits **0** when the map resolves, even while reporting gaps.
- `--release` exits **1** for incomplete/failed/unexecuted acceptance, including M10.
- Malformed maps, missing functions, incompatible/invalid receipts or hash mismatches
  exit **2**. Output paths are exclusive and never overwritten.
- `--junit PATH` can be repeated. JUnit failures/skips stay failed/unexecuted.
  Passing mapped test observations are `synthetic_verified`, never `live_pass`.
- An existing handover, runtime version string or schema fixture does not establish
  native/browser acceptance. Previously green feature evidence is retained where
  it already lives, not relabelled as a new composed product journey.

## Parent-owned release receipt input

Pass `--receipts PATH --application-commit FULL_SHA`. The receipt document contains
`receipts` for case/layer checks and `journeys` for M01–M14, SSH and performance.
Every receipt requires these fields:

```json
{
  "case_id": "C15",
  "layer": "native",
  "status": "blocked",
  "evidence_type": "native_runtime_capture",
  "application_commit": "FULL_40_CHARACTER_APPLICATION_SHA",
  "contract_revision": "1.0.0",
  "fixture_revision": "EXACT_FIXTURE_IDENTITY",
  "command": "ACTUAL_EXECUTED_COMMAND_OR_BLOCKED_PLANNED_COMMAND",
  "runtime_identity": {"sdk": "EXACT_VERSION", "cli": "EXACT_VERSION_AND_HASH"},
  "artifacts": [],
  "limits": ["Real native relocation/history check not yet executed"]
}
```

This is a **shape example**, not an executable/passing receipt. Passing receipts
require actual artefacts as `{"path":"repo/relative/evidence","sha256":"64_HEX"}`.
The validator verifies those bytes. It rejects unknown or unvalidated application
revisions, a different contract revision, and `offline_fixture` / TCP transport doubles as live product
evidence. Source commit identity refers to the tested application revision, not
the later commit adding its evidence. Native receipts use `native_runtime_capture`.
Browser release receipts use `browser_product_capture`, not component harnesses.
SSH uses `ssh_authenticated_product_capture`. Performance uses `host_measurement`.
Known limitations keep the corresponding requirement incomplete.

Journey receipt layers are `journey`, except `SSH` uses `ssh`, `PERFORMANCE` uses
`performance`, and M10 uses `native`. The required browser layers are exactly
`browser:chromium`, `browser:brave`, `browser:edge`, with installed versions,
viewport/DPR, actual decode/events/upload/reconnect evidence and artefact hashes.
No receipt is auto-created from a friendly boolean in a fixture.

The `m10` object defaults to blocked. A scope change requires `status:
"owner_scope_change"`, `actor: "human_owner"`, a non-empty `decision_reference`
and hashed decision `artifacts`. The validator checks the record, **not the identity
or authority of its author**. The parent must obtain/review the actual owner's
decision. A failed mesh proof or coordinator optimism cannot supply one.

Release readiness still requires human review of evidence adequacy. Structural
validation is not a cryptographic attestation, native runtime probe or creative
owner selection. W10 supplies the orchestration and refusal behaviour, not a full
release or a fabricated M01–M14 pass record.
