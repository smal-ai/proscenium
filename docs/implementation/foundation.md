# F00 shared foundation

Baseline: specification 3.2.0, amended contract 1.0.0, source commit
`2f00ebece9c5274ebda553df4c1558d5572669e0`. Implemented on 14 November 2026.
This is shared foundation evidence, not product milestone or native-runtime acceptance.

## Install, generate and run

```bash
make bootstrap
make generate
make spec-check lint typecheck test-unit test-contracts test-integration-offline build
uv run python -m proscenium --frontend-dir apps/frontend/dist --pair
```

Use the printed pairing URL only in the trusted operator/browser channel. Never paste its
fragment into an agent session. The minimal bootstrap does not yet contain the pairing UI.
The operator can POST its `OwnerBootstrap` using the browser application once W04 is composed.
No model, native application, runtime client or provider is started by imports, application
startup, configuration reads, project restoration or job reads.

- `uv.lock` freezes the Python dependency graph. Development uses installed CPython 3.12.13.
  The package supports Python >=3.11.
- `package-lock.json` freezes the Node graph. Development uses Node 24.18.0, npm 11.16.0.
- E01 owns the SDK/CLI compatibility proof. No guessed SDK dependency is installed here.
  The parent must integrate its accepted exact SDK/runtime pin before native execution.
- `make test-browser`, `make test-native PROFILE=...` and
  `make test-browser-installed BROWSER=brave|edge` fail explicitly while their real suites
  are absent. They do not claim a browser/native test passed. The native runner deliberately
  refuses execution until the parent integrates bounded allocation/resource validation.
- Feature integration tests are offline by default. Mark every native/model test `native`
  and exclude it from the offline command. Native tests never belong in the unit suite.

## Contracts and generated bindings

The source remains `docs/contracts/schema.json`. Do not hand-maintain DTOs.

```python
from proscenium.contracts import validate, canonical
from proscenium.contracts.generated import JobAdmission

wire = validate("JobAdmission", incoming_json)  # Returns the original JSON value or DomainError.
command = JobAdmission.model_validate(incoming_json)
wire = command.model_dump(mode="json")
assert command.root == wire
fingerprint_bytes = canonical(wire)  # RFC 8785, not json.dumps(sort_keys=True).
```

Python bindings are generated named Pydantic `RootModel` classes, not attribute-based
Pydantic object DTOs. `.root` and `.model_dump()` preserve the exact JSON representation,
including null versus omission. JSON Schema 2020-12 performs validation before Pydantic,
including closed objects, conditional guards, formats and union constraints, without
Pydantic coercion. `model_json_schema()` returns the original definition and catalogue.
Use `.model_validate()`/`.model_validate_json()`, not unchecked `.model_construct()`.
Contracts still require operation-specific relationship/content checks.

`scripts/generate_contracts.py` generator version 1 produces the Python classes, packaged
schema/transitions, schema SHA-256 and TS operation map. `json-schema-to-typescript==15.0.4`
generates all 119 named TypeScript structural definitions. TypeScript cannot enforce
numeric bounds, regexes or general JSON Schema conditionals. Runtime API validation remains
authoritative. No alternative wire shape is introduced to work around that limitation.
`make check-generated` checks both generators without editing their outputs.

```ts
import { ApiClient } from "../api";
const api = new ApiClient();
api.setCsrfToken(pairedSession.csrf_token);
const page = await api.call("listJobs", {
  path: { project_id: "project-id" }, query: { limit: 50 },
});
```

`Operations` and `routes` are generated from normative OpenAPI. `ApiClient.call` supplies
same-origin cookies, JSON content type, CSRF and the caller's original idempotency key.
It performs no automatic mutation retries. Errors throw `ApiError` with the exact error
envelope. The browser client deliberately has no bearer-token option. Internal operation
types describe the shared protocol, not browser authority to invoke them.

## Composition and dependencies

```python
from fastapi import APIRouter, Depends
from proscenium.app import create_app
from proscenium.services import Services, get_services
from proscenium.api.dependencies import owner, owner_websocket, tool, tool_or_worker, command

feature_router = APIRouter()
# Define owned routes only, using Depends(owner/tool/tool_or_worker).
app = create_app(settings, routers=[feature_router])
```

`create_app(settings: Settings | None = None, *, routers: Sequence[APIRouter] = (),
services: Services | None = None) -> FastAPI` installs only F00 routes plus explicitly
supplied feature routers. It does not import feature packages speculatively. The lifespan
creates and closes the container unless a caller supplied it. A supplied container remains
the caller's responsibility. Missing feature routes return a contract error, never a fake
successful response. `/health` reports only foundation-process health.

`Services.create(settings) -> Services` acquires the machine OS lock and closes the
execution gate on every boot. `services.close()` releases held locks. The dependency
`get_services(request) -> Services` reads `request.app.state.services`.

Public container members:

| Member | Interface |
| --- | --- |
| `settings` | Immutable `Settings`, `Settings.load(path=None)`, `.view() -> ConfigurationView` JSON. |
| `ownership` | `.epoch`, `.pid`, `.process_start`, OS-held lock, no timestamp takeover. |
| `registry` | `Registry` below. |
| `auth` | `Auth` below. |
| `gate` | `require(project_id)`, `close(project_id)`, `resume(project_id, reconciliation_evidence=str)`. |
| `jobs` | `JobService` below. |
| `resources` | Advisory cooperating-job claims below. |
| `cursors` | `page(scope, records, key, *, cursor=None, limit=50)`, HMAC scope-bound cursors. |
| `runtime` | Optional W01 `RuntimeAdapter` protocol. None until a tested adapter is installed. |
| `extensions` | Explicit service registry. `extension(name)` raises `capability_unavailable` when absent. |

`api/interfaces.py` defines `ArtifactService`, `SourceService`, `ReviewStore` and
`ContextService` protocol seams. Register accepted implementations in `extensions` using
`artifacts`, `sources`, `reviews` and `contexts`. These protocols contain no pretend handlers.
Feature packages may propose interface additions to the parent without editing shared files.

## Storage, migrations and transactions

- Machine registry: `<Settings.data_directory>/registry.sqlite3`, default
  `$XDG_DATA_HOME/proscenium/registry.sqlite3`, normal XDG fallback.
- Project records: `<canonical project>/.proscenium/state.sqlite3`.
- Machine and project locks are OS-held `flock` files, not expiring PID leases.
- SQLite uses WAL, foreign keys, synchronous FULL, a 5-second busy timeout and explicit
  `user_version` migrations. One ASGI worker only. Each transaction has its own connection.
- Database files are mode 0600 and application-created directories mode 0700. Runtime
  state, dependency environments and generated caches are ignored by Git.

```python
store = services.registry.project_store(project_id)  # Active project by default.
with store.transaction() as tx:                    # BEGIN IMMEDIATE.
    result, replayed = tx.operation("feature.command", operation_id, immutable_body, effect)
```

`effect() -> dict` must perform database-only work on that same `tx`. It may create records,
emit events and enqueue external intents. It must not await or invoke an external service.
The operation fingerprint and result commit atomically with all changes. A matching retry
returns the original result. Conflicting reuse is `idempotency_conflict`. A new resource
starts at revision zero. An update requires its current storage revision.

`Transaction` callable signatures:

```python
get(namespace: str, identity: str, *, required: bool = True) -> dict | None
require(namespace: str, identity: str) -> dict
put(namespace: str, identity: str, body: dict, *, expected_revision: int | None = None,
    schema: str | None = None) -> int
revision(namespace: str, identity: str) -> int
list(namespace: str, *, after: str = "", limit: int = 100) -> list[dict]
all(namespace: str) -> list[dict]
operation(scope: str, identity: str, body: JSON, effect: Callable[[], dict]) -> tuple[dict, bool]
emit(event_type: str, payload: dict, *, actor: dict, request_id: str | None = None,
     work_id: str | None = None, native_session_id: str | None = None,
     native_event_id: str | None = None) -> Event JSON
watermark() -> int
events(after: int = 0, limit: int = 50) -> EventPage JSON
enqueue(identity: str, kind: str, body: dict) -> None
claim_intent(identity: str) -> dict
settle_intent(identity: str, state: str, evidence: dict) -> None
```

`expected_revision=None` means **create only**, not unconditional overwrite. Records with
`state_revision` must match the new storage revision. Other records use `tx.revision()`
for storage CAS without adding fields to the wire DTO. Always pass the normative `schema`
for a public record. Project ID mismatches are rejected. Immutable snapshots use create
only inside an idempotent operation. Never update their original submission/context.

The shared physical tables are `records`, `operations`, `events`, `outbox`,
`budget_reservations` and `resource_claims`. Feature-owned namespaces permit independent
implementation without independent migration revisions:

| Namespace | Key and owning consumer |
| --- | --- |
| `work` | work_id, WorkRecord, W01. |
| `requests`, `contexts`, `decisions`, `invocations` | Their stable IDs, W01. Invocation state must settle before switching. |
| `items`, `versions`, `artifacts` | Stable IDs, W02. An artifact record's `state` must be `ready` for job input. |
| `jobs`, `attempts`, `job_inputs`, `job_evidence` | F00 only. Extensions call JobService, never overwrite these directly. |
| `annotations`, `selections`, `reviews`, `findings` | Stable IDs, W05/W07 through accepted service boundary. |
| `frames`, `segments` | receipt_id/segment_id, W03. |
| `projects`, `registry`, `private`, `auth.*` | Machine-store only, never project content or public events. |

Additional namespaced record collections require documentation, not a second database.
Relational performance indexes or new physical tables require a parent-owned migration.
`Store.backup(destination: Path)` uses SQLite's consistent backup API, refuses overwrite
and requires an existing destination directory. Before upgrading an existing schema,
the migration owner creates a versioned backup. A failed migration rolls back all DDL and
keeps the previous version. A future schema is rejected. Native-session backup additionally
requires the runtime's settled/consistent-snapshot proof and is not supplied by SQLite.

Events are schema-validated, append-only and sequence-allocated in the same transaction.
Native deduplication uses `(native_session_id, native_event_id)`, never the event ID alone.
Broadcast only after leaving the transaction successfully. A snapshot reads records and
`tx.watermark()` in one `transaction(write=False)`. Ahead-of-journal cursors expire rather
than silently returning an empty history. Public callers must supply already redacted,
allowlisted payloads. Do not send raw SDK events, headers, findings or private reasoning.

`db.outbox.dispatch_one(store, identity, gate, handlers)` commits `dispatching` before
calling a handler outside the transaction. An exception/lost acknowledgement preserves
`outcome_uncertain`. `dispatching`/uncertain rows are never automatically requeued. The
handler returns private acknowledgement evidence, not an assertion that the job succeeded.
Dispatch is explicit, never a startup side effect. Cancellation intents may run while the
execution gate is closed. Parent-installed workers must consume only their owned kinds.

## Registry, activation and paths

```python
Registry.register(record: ProjectRecord JSON) -> None
Registry.get(project_id: str) -> ProjectRecord JSON
Registry.active_id() -> str | None
Registry.project_store(project_id: str, *, active: bool = True) -> Store
Registry.assert_settled(project_id: str) -> None
Services.activate_project(project_id: str) -> None
Services.acquire_project(project_id: str) -> None
```

W02 validates `project.json`, repository identity and its owner command before registration.
The registry prevents one project ID at two locations and one location with two project IDs.
Use `Services.activate_project`, not the lower-level registry primitive, to coordinate the
project lock, settlement check and closed gate. W02 implements the revisioned HTTP receipt,
rebind verification and event transaction. Held requests remain in their original store.
Settlement checks work, invocations/children, decisions, non-terminal jobs and ambiguous
outbox dispatch. Runtime idle is not settlement.

`db.paths.project_path(root: Path, relative: str, *, managed=False, directory=False,
must_exist=True) -> Path` checks lexical paths and real-path containment, including symlinks.
It rejects traversal, file URIs, `.git`, secret/environment paths and runtime authentication
stores. `.` is accepted only for an explicitly requested directory. Trusted media/source
services may opt into managed artefact/retained/snapshot/job/tool-output roots. Even that
option never permits `.proscenium/state.sqlite3` or lock files. It is not a wire flag.
Use artifact IDs and hash/ready-state checks before resolving registered media. Do not
expose this helper as a general filesystem HTTP endpoint. Native shell/file tools retain
normal user permissions under the instructed project-only policy, not an OS sandbox.

## Authentication and HTTP dependencies

```python
Auth.issue_pairing() -> str                        # Trusted operator only, 15 minutes.
Auth.pair(body: OwnerBootstrap JSON) -> tuple[OwnerSession JSON, cookie: str, replayed: bool]
Auth.issue_capability(caller: Caller) -> str        # Trusted admission only, out of band.
Auth.revoke(token: str) -> None
Auth.owner(request: Request) -> Caller
Auth.owner_socket(socket: WebSocket) -> Caller
Auth.capability(request: Request, channels: set[str]) -> Caller
```

`Caller(channel, subject_id, project_id=None, work_id=None, owner_epoch=None, job_id=None,
attempt_id=None, input_digest=None, operations=(), invocation_id=None)` is an immutable host object, never a
model-supplied actor. Tool capabilities require admitted work, the owner epoch and explicit
operation names. Worker capabilities require exact project/job/attempt/input digest. Worker
receipts for surviving admitted jobs remain valid after restart, but stale tool epochs are
fenced. Owner cookies cannot authenticate internal endpoints. Tool/worker tokens cannot
authenticate owner endpoints. In-process tools call the same service with a host-derived
Caller, not a DTO's claimed actor/work.

Pairing consumes the unpredictable code once and binds retries to operation ID plus client
nonce. Secrets live only in private machine records. The owner cookie is HttpOnly,
SameSite=Strict, secure on HTTPS and server-renewed for 12 hours on authenticated activity.
Owner mutations require the exact Origin and `X-CSRF-Token`. All HTTP requests validate
Host, supplied Origin and same-origin browser writes. WebSocket upgrades validate Origin
before feature handlers authenticate the owner cookie. There is no permissive CORS setup.

`api.dependencies.command(request, schema, *, project_id=None, job_id=None)` requires
JSON content type, validates the schema, checks path/body identities and checks
`Idempotency-Key` against the command identity. Binary upload is W02-owned and follows its
path upload_id, not this JSON helper. Its handler must apply equivalent identity checks.
`DomainError(code, status=400, message=None, **details)` produces the shared Error envelope.
Unexpected errors are redacted. Do not place native secrets into error details.

## Jobs, attempts, budgets and resources

Implemented F00 HTTP operation IDs are exactly those in `operation-owners.json`:
`pairOwnerSession`, `getConfigurationView`, `listJobs`, `getJob`, `listJobAttempts`,
`requestJobCancellation`, `admitToolJob`, `retryToolJob`, `updateJobState`.

```python
JobService.admit(caller: Caller, body: JobAdmission JSON) -> tuple[JobRecord JSON, replayed: bool]
JobService.get(project_id: str, job_id: str) -> JobRecord JSON
JobService.retry(caller: Caller, body: JobRetry JSON) -> tuple[JobRecord JSON, bool]
JobService.update(caller: Caller, body: JobStateUpdate JSON, *, evidence: str | None = None) -> tuple[JobRecord JSON, bool]
JobService.cancel(caller: Caller, job_id: str, body: RevisionCommand JSON) -> tuple[CommandReceipt JSON, bool]
JobService.create(tx: Transaction, *, job_id: str, kind: str, target: JobTarget JSON,
                  input_digest: str, operation_id: str, work_id: str | None = None,
                  owner_operation_id: str | None = None, provider_id: str | None = None,
                  render_spec: RenderSpec JSON | None = None, expected_frames: int | None = None,
                  enqueue_kind: str | None = None) -> JobRecord JSON
JobService.emit(tx: Transaction, job: JobRecord JSON, caller: Caller) -> None
```

`create` is a trusted extension primitive for W02 publication preview intents and W07
budget/review admission in their existing transaction. Validate input/target associations,
effective configuration and caller authority before calling it. It durably creates attempt
1 and optionally its launch intent. Emit the job event in that same transaction. An idle
owner review passes `work_id=None, owner_operation_id=original_review_id`. It must not
attach itself to whatever Copilot work happens to be active.

Admit/retry allocate, but do not launch, a CLI. They return 202 for acceptance and 200 for a
matching retry. No launch capability is included in JobRecord. The trusted adapter issues
the exact worker capability out of band. Admission requires a configured execution profile,
open execution gate, active work, ready input artefacts and storage reserve. Effective
inputs/configuration are stored privately in `job_inputs` and do not change with defaults.

State updates follow the normative transition catalogue, use job CAS and bind the admitted
attempt. Unknown exit codes remain absent/null. Terminal/reconciled states require trusted
evidence. The exact-attempt worker update itself is evidence, while tool HTTP results use
observed non-null native exit codes or an in-process adapter's explicit evidence. Late old
attempt writes cannot mutate the current attempt. Retry requires verified failed/cancelled
local render/encode/Unity jobs, creates a new attempt and retains the old terminal record.
Provider inference and VLM reviews do not use local JobRetry.

Register supported cancellation adapters in `jobs.cancellers[kind]`. Unsupported cancellation
returns 501 without changing the job. Cancellation persists `cancel_requested` and a
`job.cancel` outbox intent. The parent installs the outbox consumer, invokes the registered
adapter outside the transaction and waits for worker/provider evidence before terminal
status. Completion winning cancellation remains `succeeded`. The bootstrap has no external
job executor and no default cancellers.

`jobs.resources` supplies trusted shared primitives:

```python
reserve_generation(tx, reservation_id, task_id, target_id, limit) -> None
settle_generation(tx, reservation_id, *, executed: bool, evidence: str) -> None
Resources.acquire(resource: str, project_id: str, job_id: str, attempt_id: str) -> bool
Resources.release(resource: str, project_id: str, job_id: str, attempt_id: str, *, terminal_evidence: str) -> None
check_storage(root: Path, reserve_bytes: int, estimated_remaining_bytes: int | None = None) -> None
```

W07 reserves within its job/admission transaction using the **host-frozen task/target limit**,
not an agent-provided limit. Reserved/ambiguous and executed attempts count. Only proven
pre-execution rejection releases a reservation. Status/poll/download does not reserve again.
Machine resource claims have capacity one per resource key, e.g. `heavy-gpu` and
`blender-render-writer`. They are advisory for cooperating jobs, not loaded model processes
or unrelated applications. No timestamp alone releases a claim. Default storage reserve is
5 GiB or estimated remaining output, whichever is larger. There is no destructive GC.

## Offline evidence and remaining integration

`tests/conftest.py` supplies disposable registry/project databases, owner pairing, a scoped
tool caller and a synthetic local-job command. `tests/fixtures/doubles.py` provides runtime
acknowledgement loss, provider uncertainty and completion-during-cancellation. These are
clearly synthetic and never access reference projects, services or GPUs.

The foundation tests cover all positive/negative schema examples, deterministic generation,
document semantic oracles, caller-channel separation, Host/Origin/CSRF, path/symlink escapes,
atomic rollback, CAS, WAL/migration rollback/backup, native session-scoped deduplication,
outbox ambiguity, budget reservation, resource ownership, retry attempts, cancellation races
and restart gating. Feature routers, actual SDK fencing, native media, browser acceptance and
full product journeys remain the respective packages' integration responsibilities.

### Recorded F00 check results

On 14 November 2026, using CPython 3.12.13 and Node 24.18.0 on Linux:

| Exact command | Result |
| --- | --- |
| `make spec-check` | Passed. 119 definitions, 40 wire examples, 53 operation ownership entries, 20 semantic oracles and deterministic bindings. |
| `make lint` | Passed Ruff checks/formatting and ESLint. |
| `make typecheck` | Passed mypy on 22 source files and frontend TypeScript. |
| `make test-unit` | 23 passed. |
| `make test-contracts` | 45 passed. |
| `make test-integration-offline` | 21 passed, including concurrent cancellation/completion CAS and HTTP/WebSocket caller boundaries. |
| `make build` | Passed Vite production build. |
| `npm audit` | Zero reported vulnerabilities. |
| `uv build --package proscenium --out-dir /tmp/opencode/proscenium-f00-dist` | Built source distribution and wheel, including schema/transition resources. |
| `make test-browser` | Expected non-zero exit 2. Missing real acceptance suite. |
| `make test-native PROFILE=foundation-offline` | Expected non-zero exit 2. Missing real native suite. No application/model execution. |
| `make test-browser-installed BROWSER=brave` | Expected non-zero exit 2. Missing installed-browser suite. |
| `make test-browser-installed BROWSER=edge` | Expected non-zero exit 2. Missing installed-browser suite. |

The installed Starlette TestClient emits one deprecation warning about its httpx transport
per pytest invocation. Tests still execute normally. No native runtime, GPU or installed
browser compatibility is inferred from these results. Specification checks validate the
reference pin manifest and local design bytes. External reference-repository byte checks
(`docs/validate_spec.py --check-references`) were not run and are not claimed here.
