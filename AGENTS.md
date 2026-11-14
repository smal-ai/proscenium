# Proscenium development

- Read `docs/specification.md`, `docs/owner-policy.md` and the assigned work package in `docs/development-fleet.md` before implementation.
- The application is implemented and undergoing cumulative journey acceptance. Documentation, synthetic fixtures and reference-project checks are not product completion evidence. Use `docs/implementation/progress.md` for accepted revisions and active ownership.
- Use the normative contracts in `docs/contracts/`. Shared schemas, generated types, dependency locks, database migrations and router composition have one foundation/integration owner.
- A work package defines its permitted write paths and accepted dependency revisions. Raise shared-interface changes to the integration owner rather than inventing a parallel schema.
- Preserve unrelated changes, reference repositories, active Blender/Unity sessions and independent model services. Inspect Git status before edits.
- The product runtime accesses only its active project. Development access to the pinned reference sources is separately scoped by the assigned task.
- Use disposable projects/databases and the documented offline fixtures. Native/model/GPU checks require an explicitly bounded test allocation and resource ownership.
- Keep credentials, environments, runtime stores and generated caches out of Git. Source history is Git/Git LFS; published media and native session state have their own retention/backup requirements.
- Do not run the archived HTML mockup as production code or restore its obsolete transaction/RAG/gameplay behaviour.
- Use British English and SAST. Record ISO 8601 dates and exact source/runtime/test identities.
- Do not dispatch development agents solely because the fleet document exists. Dispatch requires a current authorised assignment.
