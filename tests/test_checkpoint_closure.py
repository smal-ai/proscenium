"""Offline checkpoint closures, publication interoperability and compatible wire records."""

import copy
import json
from types import SimpleNamespace

import httpx
import pytest
from copilot import CopilotClient
from proscenium.api.errors import DomainError
from proscenium.auth import Caller
from proscenium.catalogue.artifacts import Artifacts
from proscenium.catalogue.service import Catalogue
from proscenium.configuration import Settings
from proscenium.contracts import canonical, validate
from proscenium.db import Store
from proscenium.jobs.gate import ExecutionGate
from proscenium.sources import Sources, closure_digest
from proscenium.sources.common import git, sha

PROJECT = "project-one"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Checkpoint closure tests must remain offline")

    async def blocked_async(*args, **kwargs):
        blocked()

    monkeypatch.setattr(CopilotClient, "start", blocked_async)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", blocked_async)


@pytest.fixture
def runtime(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Offline fixture")
    git(root, "config", "user.email", "fixture@proscenium.invalid")
    git(root, "commit", "--allow-empty", "-m", "Initial fixture")
    store = Store(root / ".proscenium/state.sqlite3", PROJECT)
    gate = ExecutionGate()
    gate.resume(PROJECT, reconciliation_evidence="Offline fixture")
    registry = SimpleNamespace(
        get=lambda project_id: dict(path=str(root), repository_id="repository-one"),
        project_store=lambda project_id: store,
        active_id=lambda: PROJECT,
    )
    services = SimpleNamespace(
        registry=registry,
        gate=gate,
        ownership=SimpleNamespace(epoch="epoch"),
        settings=Settings(data_directory=tmp_path / "machine"),
    )
    caller = Caller(
        "tool",
        "fixture",
        project_id=PROJECT,
        work_id="work-one",
        owner_epoch="epoch",
        operations=("checkpoint_sources", "publishOutputs"),
        invocation_id="invocation-one",
    )
    with store.transaction() as tx:
        tx.put(
            "work",
            "work-one",
            {
                "state": "running",
                "native_session_id": "session-one",
                "originating_request_id": "request-one",
            },
        )
        tx.put(
            "invocations",
            "invocation-one",
            dict(work_id="work-one", owner_epoch="epoch", state="running"),
        )
    sources = Sources(services)
    return SimpleNamespace(
        root=root, store=store, services=services, caller=caller, sources=sources
    )


def request(runtime, **changes):
    return dict(
        schema_version="1.0",
        operation_id="checkpoint-one",
        project_id=PROJECT,
        paths=["plan.json"],
        message="Checkpoint offline fixture",
        expected_head=git(runtime.root, "rev-parse", "HEAD").decode().strip(),
        **changes,
    )


def write_plan(runtime):
    plan = dict(
        schema_version="1.0",
        plan_id="plan-one",
        project_id=PROJECT,
        brief_artifact_ids=[],
        asset_dependencies=[],
        unresolved=[],
        shots=[
            dict(
                shot_id="shot-one",
                intent="Wide establishing shot",
                camera_intent="Static",
                motion_beats=[],
                frames=[],
                scene_version=None,
                source_range=None,
                fps=None,
            )
        ],
    )
    validate("AuthoredShotPlan", plan)
    (runtime.root / "plan.json").write_bytes(canonical(plan))
    return plan


def retain(runtime, identity="retained-one", data=b"Retained fixture"):
    digest = sha(data)
    path = runtime.root / f".proscenium/retained/{digest}/content"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    with runtime.store.transaction() as tx:
        tx.put("retained_sources", identity, dict(sha256=digest))
    return dict(kind="retained", artifact_id=identity, sha256=digest)


def test_legacy_checkpoint_omits_closure(runtime):
    write_plan(runtime)
    body = request(runtime)
    receipt = runtime.sources.checkpoint(runtime.caller, body)
    assert "source_closure" not in receipt
    replay = runtime.sources.checkpoint(runtime.caller, body)
    assert replay == {**receipt, "replayed": True}


def test_checkpoint_closure_resolves_and_publishes_storyboard(runtime):
    plan = write_plan(runtime)
    receipt = runtime.sources.checkpoint(runtime.caller, request(runtime, entry_path="plan.json"))
    closure = receipt["source_closure"]
    validate("SourceClosure", closure)
    assert closure["dependencies"] == []
    assert closure["required_runtime"] == dict(application="proscenium", version="1.0", features=[])
    assert closure["digest"] == closure_digest(closure)
    snapshot = runtime.sources.resolve(PROJECT, closure)
    assert json.loads((snapshot / "plan.json").read_bytes()) == plan
    catalogue = Catalogue(runtime.services, runtime.sources, Artifacts(runtime.services))
    publication, repeated = catalogue.publish(
        runtime.caller,
        dict(
            schema_version="1.0",
            publication_id="publication-one",
            project_id=PROJECT,
            items=[
                dict(
                    item_id="storyboard-one",
                    version_id="version-one",
                    kind="storyboard",
                    name="Offline storyboard",
                    expected_current_version_id=None,
                    source_commit=receipt["source_commit"],
                    source=closure,
                    artifact_ids=[],
                    preview_requests=[],
                )
            ],
        ),
    )
    assert not repeated
    assert publication["versions"] == [dict(item_id="storyboard-one", version_id="version-one")]
    with runtime.store.transaction(write=False) as tx:
        assert tx.require("versions", "version-one")["source"] == closure


def test_closure_canonical_order_and_replay(runtime):
    write_plan(runtime)
    (runtime.root / "z.txt").write_text("Git fixture")
    refs = [
        dict(item_id="z-asset", version_id="z-version"),
        dict(item_id="a-asset", version_id="a-version"),
    ]
    with runtime.store.transaction() as tx:
        for ref in refs:
            tx.put("versions", ref["version_id"], dict(project_id=PROJECT, **ref))
    retained = [
        dict(destination="y.txt", origin=retain(runtime)),
        dict(destination="a.txt", origin=retain(runtime, "retained-two", b"Second fixture")),
    ]
    body = request(
        runtime,
        entry_path="plan.json",
        dependencies=refs,
        required_runtime=dict(application="blender", version="4.5", features=["cycles", "python"]),
        retained_files=retained,
    )
    body["paths"] = ["z.txt", "plan.json"]
    receipt = runtime.sources.checkpoint(runtime.caller, body)
    closure = receipt["source_closure"]
    assert [row["destination"] for row in closure["files"]] == [
        "a.txt",
        "plan.json",
        "y.txt",
        "z.txt",
    ]
    assert closure["dependencies"] == list(reversed(refs))
    assert closure["required_runtime"] == body["required_runtime"]
    assert closure["digest"] == closure_digest(closure)
    assert runtime.sources.checkpoint(runtime.caller, body) == {**receipt, "replayed": True}
    snapshot = runtime.sources.resolve(PROJECT, closure)
    assert (snapshot / "a.txt").read_bytes() == b"Second fixture"


@pytest.mark.parametrize(
    "case,code",
    [
        ("missing_entry", "dependency_missing"),
        ("retained_without_entry", "invalid_request"),
        ("dependencies_without_entry", "invalid_request"),
        ("runtime_without_entry", "invalid_request"),
        ("duplicate", "dependency_collision"),
        ("prefix", "dependency_collision"),
        ("duplicate_paths", "dependency_collision"),
        ("missing_version", "dependency_missing"),
        ("wrong_item", "scope_denied"),
        ("wrong_project", "scope_denied"),
        ("missing_retained", "retained_source_missing"),
        ("missing_content", "retained_source_missing"),
        ("corrupt_content", "retained_source_missing"),
        ("wrong_retained_hash", "retained_source_missing"),
        ("receipt_entry", "scope_denied"),
        ("receipt_destination", "scope_denied"),
        ("secret_entry", "scope_denied"),
    ],
)
def test_invalid_closure_does_not_write_intent_or_refs(runtime, case, code):
    write_plan(runtime)
    body = request(runtime, entry_path="plan.json")
    origin = retain(runtime)
    if case == "missing_entry":
        body["entry_path"] = "absent.json"
    elif case.endswith("without_entry"):
        del body["entry_path"]
        key = case.removesuffix("_without_entry")
        key = (
            "retained_files"
            if key == "retained"
            else "required_runtime"
            if key == "runtime"
            else key
        )
        body[key] = (
            dict(application="proscenium", version="1.0", features=[])
            if key == "required_runtime"
            else []
        )
    elif case in {"duplicate", "prefix", "receipt_destination"}:
        destination = {
            "duplicate": "plan.json",
            "prefix": "plan.json/child",
            "receipt_destination": ".receipt.json/child",
        }[case]
        body["retained_files"] = [dict(destination=destination, origin=origin)]
    elif case == "duplicate_paths":
        body["paths"] *= 2
    elif case in {"missing_version", "wrong_item", "wrong_project"}:
        body["dependencies"] = [dict(item_id="asset-one", version_id="version-one")]
        if case != "missing_version":
            with runtime.store.transaction() as tx:
                tx.put(
                    "versions",
                    "version-one",
                    dict(
                        item_id="wrong" if case == "wrong_item" else "asset-one",
                        project_id=PROJECT,
                    ),
                )
                if case == "wrong_project":
                    # Deliberately corrupt a stored row, bypassing the store's write-time guard.
                    tx.connection.execute(
                        "UPDATE records SET body=json_set(body, '$.project_id', 'wrong') "
                        "WHERE namespace='versions' AND id='version-one'"
                    )
    elif case in {"receipt_entry", "secret_entry"}:
        body["entry_path"] = ".receipt.json" if case == "receipt_entry" else ".env"
    else:
        body["retained_files"] = [dict(destination="retained.txt", origin=origin)]
        path = runtime.root / f".proscenium/retained/{origin['sha256']}/content"
        if case == "missing_retained":
            origin["artifact_id"] = "absent"
        elif case == "missing_content":
            path.unlink()
        elif case == "corrupt_content":
            path.write_bytes(b"Corrupt")
        elif case == "wrong_retained_hash":
            origin["sha256"] = "0" * 64
    head = git(runtime.root, "rev-parse", "HEAD")
    with pytest.raises(DomainError) as error:
        runtime.sources.checkpoint(runtime.caller, body)
    assert error.value.code == code
    assert git(runtime.root, "rev-parse", "HEAD") == head
    assert git(runtime.root, "for-each-ref", "refs/proscenium/checkpoints/") == b""
    with runtime.store.transaction(write=False) as tx:
        assert tx.get("checkpoint_intents", body["operation_id"], required=False) is None


def test_replay_verifies_retained_bytes(runtime):
    write_plan(runtime)
    origin = retain(runtime)
    body = request(
        runtime,
        entry_path="plan.json",
        retained_files=[dict(destination="retained.txt", origin=origin)],
    )
    runtime.sources.checkpoint(runtime.caller, body)
    path = runtime.root / f".proscenium/retained/{origin['sha256']}/content"
    path.write_bytes(b"Tampered")
    with pytest.raises(DomainError) as error:
        runtime.sources.checkpoint(runtime.caller, body)
    assert error.value.code == "retained_source_missing"


def test_replay_rejects_tampered_saved_closure(runtime):
    write_plan(runtime)
    body = request(runtime, entry_path="plan.json")
    receipt = runtime.sources.checkpoint(runtime.caller, body)
    receipt["source_closure"]["digest"] = "0" * 64
    with runtime.store.transaction() as tx:
        tx.connection.execute(
            "UPDATE operations SET result=? WHERE scope='sources.checkpoint' AND id=?",
            (canonical(receipt).decode(), body["operation_id"]),
        )
    with pytest.raises(DomainError) as error:
        runtime.sources.checkpoint(runtime.caller, body)
    assert error.value.code == "source_conflict"


def test_retained_entry_resolves_without_adding_retained_bytes_to_git(runtime):
    (runtime.root / "source.txt").write_text("Git source")
    origin = retain(runtime, data=b"Retained entry")
    body = request(
        runtime,
        entry_path="entry.txt",
        retained_files=[dict(destination="entry.txt", origin=origin)],
    )
    body["paths"] = ["source.txt"]
    receipt = runtime.sources.checkpoint(runtime.caller, body)
    snapshot = runtime.sources.resolve(PROJECT, receipt["source_closure"])
    assert (snapshot / "entry.txt").read_bytes() == b"Retained entry"
    assert git(runtime.root, "ls-tree", receipt["source_commit"], "--", "entry.txt") == b""


def test_closure_revalidated_after_preparation_before_refs(runtime, monkeypatch):
    write_plan(runtime)
    origin = retain(runtime)
    body = request(
        runtime,
        entry_path="plan.json",
        retained_files=[dict(destination="retained.txt", origin=origin)],
    )
    original = runtime.sources.checkpoints.prepare
    head = git(runtime.root, "rev-parse", "HEAD")

    def prepare_then_corrupt(*args, **kwargs):
        candidate = original(*args, **kwargs)
        path = runtime.root / f".proscenium/retained/{origin['sha256']}/content"
        path.write_bytes(b"Changed after admission")
        return candidate

    monkeypatch.setattr(runtime.sources.checkpoints, "prepare", prepare_then_corrupt)
    with pytest.raises(DomainError) as error:
        runtime.sources.checkpoint(runtime.caller, body)
    assert error.value.code == "retained_source_missing"
    assert git(runtime.root, "rev-parse", "HEAD") == head
    assert git(runtime.root, "for-each-ref", "refs/proscenium/checkpoints/") == b""


def test_prepared_intent_crash_recovers_closure(runtime, monkeypatch):
    write_plan(runtime)
    body = request(runtime, entry_path="plan.json")
    original = runtime.sources.checkpoints.prepare
    head = git(runtime.root, "rev-parse", "HEAD")

    def crash(*args, **kwargs):
        raise RuntimeError("Simulated prepared intent crash")

    monkeypatch.setattr(runtime.sources.checkpoints, "prepare", crash)
    with pytest.raises(RuntimeError, match="prepared intent crash"):
        runtime.sources.checkpoint(runtime.caller, body)
    assert git(runtime.root, "rev-parse", "HEAD") == head
    monkeypatch.setattr(runtime.sources.checkpoints, "prepare", original)
    receipt = runtime.sources.checkpoint(runtime.caller, body)
    assert receipt["replayed"]
    validate("SourceClosure", receipt["source_closure"])
    assert runtime.sources.checkpoint(runtime.caller, body) == receipt


def test_crash_after_ref_update_recovers_identical_closure(runtime, monkeypatch):
    write_plan(runtime)
    body = request(runtime, entry_path="plan.json")
    original = runtime.sources.checkpoints.source_closure

    def crash(*args, **kwargs):
        raise RuntimeError("Simulated receipt crash")

    monkeypatch.setattr(runtime.sources.checkpoints, "source_closure", crash)
    with pytest.raises(RuntimeError, match="receipt crash"):
        runtime.sources.checkpoint(runtime.caller, body)
    commit = git(runtime.root, "rev-parse", "HEAD").decode().strip()
    monkeypatch.setattr(runtime.sources.checkpoints, "source_closure", original)
    receipt = runtime.sources.checkpoint(runtime.caller, body)
    assert receipt["replayed"] and receipt["source_commit"] == commit
    assert receipt["source_closure"]["digest"] == closure_digest(receipt["source_closure"])
    assert runtime.sources.checkpoint(runtime.caller, body) == receipt


def test_uncertain_checkpoint_does_not_claim_closure(runtime, monkeypatch):
    write_plan(runtime)
    body = request(runtime, entry_path="plan.json")
    from proscenium.sources import checkpoint

    def uncertain_git(root, *args, **kwargs):
        if args[0] == "update-ref":
            raise DomainError("source_unavailable", 422)
        return git(root, *args, **kwargs)

    monkeypatch.setattr(checkpoint, "git", uncertain_git)
    receipt = runtime.sources.checkpoint(runtime.caller, body)
    assert receipt["state"] == "outcome_uncertain" and receipt["source_commit"] is None
    assert receipt.get("source_closure") is None
    validate("CheckpointReceipt", receipt)
    invalid = {**receipt, "source_closure": {}}
    with pytest.raises(DomainError):
        validate("CheckpointReceipt", invalid)


def test_compatible_provider_and_preparation_contracts():
    provider = dict(
        provider_id="provider-one",
        capability="image",
        status="configured",
        profile_revision=None,
        selected_models=[],
        compatible_models=[],
        reference_dimensions=None,
        reason_code=None,
        summary="Offline fixture",
    )
    validate("ProviderCapabilitySummary", provider)
    for extensions in (
        dict(default_dimensions=None, default_steps=None, max_pixels=None),
        dict(default_dimensions=dict(width=512, height=512), default_steps=4, max_pixels=262144),
    ):
        validate("ProviderCapabilitySummary", {**provider, **extensions})
    for key, value in (
        ("default_steps", 0),
        ("default_steps", 51),
        ("default_steps", True),
        ("max_pixels", 4095),
        ("default_dimensions", dict(width=0, height=512)),
    ):
        with pytest.raises(DomainError):
            validate("ProviderCapabilitySummary", {**provider, key: value})
    prepare = dict(
        schema_version="1.0", operation_id="prepare-one", prompt="Synthetic prompt", seed="42"
    )
    validate("ImageGenerationPrepare", prepare)
    validate(
        "ImageGenerationPrepare",
        {**prepare, "project_id": PROJECT, "generation_task_id": "task-one"},
    )
    for key in ("project_id", "generation_task_id", "seed", "prompt"):
        invalid = copy.deepcopy(prepare)
        invalid[key] = None
        with pytest.raises(DomainError):
            validate("ImageGenerationPrepare", invalid)
