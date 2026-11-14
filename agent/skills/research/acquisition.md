# Scoped acquisition and validation

- Resolve every destination against the active project's canonical real root.
  Reject absolute/traversal/archive-member paths, escaping symlinks, credentials,
  environment files and runtime stores. Check parent directories before creating
  a target. Never follow an archive's links or execute its bundled scripts.
- Preserve existing files. Inspect receipts and fresh hashes before reuse. Changed
  bytes get a new identity, not a rewritten historical receipt. Stage new bytes to
  a unique temporary target with timeouts, bounded retries and streaming per-file
  and total-byte caps. Count extraction and derivatives against storage limits.
- Check HTTP status, signature and parser/decoder results. A login page saved as
  `.glb` is a failed acquisition. Verify archive CRC, member paths, expansion limits
  and dependencies before promoting files. Preserve useful failures without tokens
  or signed URLs. Do not bypass login, payment or certificate checks.
- Save original archives. Record extracted children with archive identity/member,
  and derivatives with exact input artefact IDs/hashes and transformation/tool.
  Conversion never establishes new rights. Use one coordinated receipt writer.
- Keep source URL, creator, licence and relevant attribution/restrictions when known.
  Null is an honest unknown and does not prevent local use or publication. Public
  sharing still needs exact destination/scope approval, irrespective of licence.

## Evidence ladder

| State/check | Required observation | Not established |
| --- | --- | --- |
| Discovered | Actual source/search result | Local bytes or editable model |
| Acquired | Local bytes, SHA-256, byte count and receipt | Valid import or useful geometry |
| Format checked | Signature and actual decode/parser result | Scale, appearance or motion |
| Imported | Application/version, command/log and resulting objects | Visual suitability or rig readiness |
| Validated | Named units/material/dependency/visual/motion checks actually run | Checks not performed or owner selection |

Use the smallest relevant checks. Offline planning never starts Blender, model
services or GPU tests. Unavailable native checks remain explicitly not run.
Checkpoint/publication uses accepted source tools and immutable receipts, not this
ledger as an alternative database. Do not mutate an existing scene on asset revision.
