# Proscenium runtime development

- Read `docs/specification.md`, `docs/owner-policy.md` and `docs/operator.md` before implementation.
- Use a topic branch and pull request for changes to `main`. Do not bypass its branch protection.
- Inspect Git status and preserve unrelated changes, projects, native sessions and independent model services.
- `docs/contracts/` contains the normative inputs. Regenerate bindings with `make generate` after an authorised contract change. Keep schemas, types, migrations and router composition coordinated.
- Run `make check` before submitting a change. The smoke tests use disposable projects with model/provider execution blocked.
- Native, model and GPU checks require an explicitly bounded allocation and ownership of every process they start.
- Keep credentials, private configuration, environments, databases, generated media, caches and model weights out of Git.
- The runtime accesses only its active project. LM Studio and ComfyUI remain independently operated services.
- Use British English, SAST and ISO 8601 dates. Preserve actual source/job/native identities and unknown outcomes.
- Historical proofs and the complete development fleet are retained on `impl/studio`. Their presence does not authorise new agent dispatch or native work.
