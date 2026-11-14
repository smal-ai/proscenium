# M04 inspection attachment-role correction

`Contexts.attachments` now explicitly maps `proxy_capture` to `reference`.
The original attachment list, target identities and existing `(artefact, role)`
deduplication are unchanged. Real Blender/sequence renders still map to `render`.

Eight deterministic cases cover primary, annotation and approved-finding target
paths, ready PNG file resolution, retained markup/masks and intentional same-image
attachments with distinct semantics. No SDK/model or camera proof was rerun.

`uv run pytest tests/integration/copilot/test_contexts.py tests/integration/copilot/test_inspection_attachment_roles.py -q`:
**21 passed**, one existing TestClient warning, 0.24 s. Targeted mypy, Ruff and
formatting pass. Parent-owned W05 camera propagation/still-profile guidance is not
changed or re-certified by this role-only fix.
