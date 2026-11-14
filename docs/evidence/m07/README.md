# M07 acceptance: blocked before the original prerequisite inference

**M07 is not complete.** The renewed original-job window stopped before submission because
actual host memory never reached the unchanged cold-fit threshold. The native/UI portion
was deliberately not started, rather than spending its allocation on a different job or
an unallocated parameter search.

- [Exact continuation observations and preserved IDs](../w07/continuation-b/README.md).
- [M07 result/counters](result.json).
- [Strict criterion disposition](../../implementation/m07.md).

This allocation performed **zero** Comfy inferences, provider prompt POSTs, cloud roots,
children or native M07 image selections. The original job remains planned with one
reservation and no provider prompt/client identity. There is no generated PNG or real UI
generation/correction screenshot to show. The raw append-only before/after receipts are
retained, not replaced with synthetic success evidence.

Only the named continuation code and its safeguards were implemented. Four focused offline
tests cover named-window authority, exclusive receipt writes, receipt replay without
service effects and known-ACK-loss transport bookkeeping with a clearly synthetic provider.
Those tests do not establish live ACK-loss recovery or native generation capability.

Targeted read-only evidence verification:

```bash
uv run python tests/native/providers/m07_export_continuation.py
PYTHONPATH=.:tests uv run pytest -q tests/native/providers/m07_continuation_test.py tests/acceptance/m07_continuation_evidence_test.py
```

The exporter and tests start no service or model. They do not resume the retained job.

Final targeted result: **8 passed**, comprising four offline continuation guards and
four retained real-evidence checks. Ruff check/format and mypy passed on the four affected
Python files. Specification validation passed with 119 definitions, 53 operations and
20 semantic oracles. Only the existing Starlette/httpx TestClient warning remains.
