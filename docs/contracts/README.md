# Contract 1.0.0

- `schema.json` defines the JSON Schema records and native tool envelopes.
- `openapi.yaml` defines the HTTP operations and shared types.
- `state-transitions.json` defines guarded work and job-attempt transitions.

Run `make generate` after an authorised change and `make check-generated` to verify
the backend/frontend bindings. Wire `schema_version` remains `1.0`.

The [complete protocol documentation](https://github.com/smal-ai/proscenium/tree/6b714c0dd574abe3995cf7020b988190336cefdf/docs/contracts)
and [execution semantics](https://github.com/smal-ai/proscenium/blob/6b714c0dd574abe3995cf7020b988190336cefdf/docs/execution.md)
are retained at the accepted implementation revision.
