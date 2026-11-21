"""Offline checkpoint closures, publication interoperability and compatible wire records."""

import asyncio
import copy
import dataclasses
import json
from types import SimpleNamespace

import httpx
import pytest
from copilot import CopilotClient
from copilot.tools import ToolInvocation
from proscenium.api.errors import DomainError
from proscenium.auth import Caller
from proscenium.catalogue.artifacts import Artifacts
from proscenium.catalogue.service import Catalogue
from proscenium.configuration import Settings
from proscenium.contracts import canonical, validate
from proscenium.copilot.capabilities import validate_authored_document
from proscenium.copilot.tools import Tools, result_text
from proscenium.db import Store
from proscenium.jobs.gate import ExecutionGate
from proscenium.sources import Sources, closure_digest
from proscenium.sources.checkpoint import prepared_branch
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
    (root / ".gitignore").write_text("/.proscenium/\n")
    git(root, "add", ".gitignore")
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
    catalogue = Catalogue(services, sources, Artifacts(services))
    services.extension = {"sources": sources, "catalogue": catalogue}.__getitem__
    return SimpleNamespace(
        root=root,
        store=store,
        services=services,
        caller=caller,
        sources=sources,
        catalogue=catalogue,
    )


def request(runtime, **changes):
    return {
        **dict(
            schema_version="1.0",
            operation_id="checkpoint-one",
            project_id=PROJECT,
            paths=["plan.json"],
            message="Checkpoint offline fixture",
            expected_head=git(runtime.root, "rev-parse", "HEAD").decode().strip(),
        ),
        **changes,
    }


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


def test_checkpoint_replays_across_resumed_invocation_and_reordered_paths(runtime):
    write_plan(runtime)
    (runtime.root / "notes.md").write_text("Brief notes")
    body = request(runtime, entry_path="plan.json")
    body["paths"] = ["notes.md", "plan.json"]
    receipt = runtime.sources.checkpoint(runtime.caller, body)
    resumed_caller = dataclasses.replace(runtime.caller, invocation_id="resumed-tool-call")
    reordered = {**body, "paths": ["plan.json", "notes.md"]}
    assert runtime.sources.checkpoint(resumed_caller, reordered) == {**receipt, "replayed": True}

    # Also verify legacy pre-fix invocation-bound operations rows replay when intent matches.
    with runtime.store.transaction() as tx:
        tx.connection.execute(
            "UPDATE operations SET fingerprint='legacy' WHERE scope='sources.checkpoint' AND id=?",
            (body["operation_id"],),
        )
        tx.connection.execute(
            "UPDATE records SET body=json_remove(body,'$.modern_binding') WHERE namespace='checkpoint_intents' AND id=?",
            (body["operation_id"],),
        )
    assert runtime.sources.checkpoint(resumed_caller, reordered) == {**receipt, "replayed": True}
    with pytest.raises(DomainError) as conflict:
        runtime.sources.checkpoint(resumed_caller, {**reordered, "message": "Different message"})
    assert conflict.value.code == "idempotency_conflict"


ENTRY = "scenes/boardroom.blend"
BASE_VERSION = {"item_id": "boardroom", "version_id": "boardroom-v1"}
BAD_RECEIPT = "Checkpoint receipt is unavailable or does not match source_commit"


def lfs_entry(runtime, data):
    root = runtime.root
    git(root, "lfs", "install", "--local", "--skip-repo")
    git(root, "lfs", "track", "*.blend")
    (root / ENTRY).parent.mkdir(exist_ok=True)
    (root / ENTRY).write_bytes(data)


def test_checkpoint_syncs_lfs_pointer_new_files_and_literal_paths(runtime):
    root = runtime.root
    lfs_entry(runtime, b"Original Blender fixture")
    git(root, "add", ".gitattributes", ENTRY)
    git(root, "commit", "-m", "Original Blender source")
    lfs_entry(runtime, b"Updated Blender fixture")
    for path in ("plan.json", "a[1].txt", "a1.txt"):
        (root / path).write_text(path)
    git(root, "add", "a1.txt")
    unaffected = git(root, "ls-files", "-s", "--", "a1.txt")
    body = request(runtime, entry_path=ENTRY, paths=[ENTRY, "plan.json", "a[1].txt"])
    receipt = runtime.sources.checkpoint(runtime.caller, body)
    assert git(root, "status", "--porcelain") == b"A  a1.txt\n"
    assert git(root, "ls-files", "-s", "--", "a1.txt") == unaffected
    pointer = git(root, "show", f":{ENTRY}")
    assert pointer == git(root, "show", f"HEAD:{ENTRY}")
    assert b"oid sha256:" + sha(b"Updated Blender fixture").encode() in pointer
    assert runtime.sources.checkpoint(runtime.caller, body) == {**receipt, "replayed": True}
    git(root, "commit", "-m", "Unrelated staging")
    assert git(root, "status", "--porcelain") == b""


@pytest.mark.parametrize("staged", ["blob", "mode", "deletion", "unmerged", "addition"])
def test_checkpoint_preserves_stage_entries_different_from_parent(runtime, staged):
    root = runtime.root
    path = root / "plan.json"
    path.write_text("Parent")
    if staged != "addition":
        git(root, "add", "plan.json")
        git(root, "commit", "-m", "Parent plan")
    if staged in {"blob", "addition"}:
        path.write_text("User staged")
        git(root, "add", "plan.json")
    elif staged == "mode":
        git(root, "update-index", "--chmod=+x", "plan.json")
    elif staged == "deletion":
        git(root, "rm", "--cached", "plan.json")
    else:
        oid = git(root, "rev-parse", "HEAD:plan.json").strip()
        git(
            root,
            "update-index",
            "--index-info",
            data=(
                b"0 "
                + b"0" * 40
                + b"\tplan.json\n"
                + b"".join(
                    b"100644 " + oid + b" " + str(i).encode() + b"\tplan.json\n" for i in (1, 2, 3)
                )
            ),
        )
    (root / "other.txt").write_text("Other staged change")
    git(root, "add", "other.txt")
    before = git(root, "ls-files", "-s", "-z")
    path.write_text("Checkpointed working content")
    runtime.sources.checkpoint(runtime.caller, request(runtime, entry_path="plan.json"))
    assert git(root, "ls-files", "-s", "-z") == before
    assert git(root, "show", "HEAD:plan.json") == b"Checkpointed working content"


def test_checkpoint_replays_index_repair_without_stealing_git_lock(runtime):
    write_plan(runtime)
    root = runtime.root
    lock = root / ".git/index.lock"
    lock.write_bytes(b"owned by another Git writer")
    body = request(runtime, entry_path="plan.json")
    receipt = runtime.sources.checkpoint(runtime.caller, body)
    assert lock.read_bytes() == b"owned by another Git writer"
    assert not git(root, "ls-files", "-s", "--", "plan.json")
    lock.unlink()
    assert runtime.sources.checkpoint(runtime.caller, body) == {**receipt, "replayed": True}
    assert git(root, "status", "--porcelain") == b""


def test_checkpoint_old_branch_replay_does_not_stage_on_current_branch(runtime):
    write_plan(runtime)
    root = runtime.root
    body = request(runtime, entry_path="plan.json")
    receipt = runtime.sources.checkpoint(runtime.caller, body)
    git(root, "switch", "-c", "different", body["expected_head"])
    assert runtime.sources.checkpoint(runtime.caller, body) == {**receipt, "replayed": True}
    assert not git(root, "status", "--porcelain")
    assert not git(root, "ls-files", "-s", "--", "plan.json")


def publication(runtime, receipt, *, ref=BASE_VERSION, previous=None):
    return dict(
        schema_version="1.0",
        publication_id=f"publish-{ref['version_id']}",
        project_id=PROJECT,
        items=[
            dict(
                **ref,
                kind="scene",
                name="Boardroom",
                expected_current_version_id=previous,
                source_commit=receipt["source_commit"],
                checkpoint_operation_id=receipt["operation_id"],
                artifact_ids=[],
                preview_requests=[],
            )
        ],
    )


def authoring_validation(runtime, body):
    return validate_authored_document(
        runtime.services,
        PROJECT,
        dict(
            schema_version="1.0",
            project_id=PROJECT,
            document_kind="publication_submit",
            document=body,
        ),
    )


def base_scene(runtime, extra=0):
    root = runtime.root
    lfs_entry(runtime, b"Boardroom v1")
    paths = [ENTRY, "scenes/old-display.txt"] + [
        f"scenes/externals/file-{i:03}.txt" for i in range(extra)
    ]
    for name in paths[1:]:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name)
    git(root, "add", ".gitattributes", *paths)
    git(root, "commit", "-m", "Base scene and its externals")
    commit = git(root, "rev-parse", "HEAD").decode().strip()
    closure = dict(
        entry_path=ENTRY,
        dependencies=[],
        required_runtime=dict(application="blender", version="4.5", features=[]),
        files=[
            dict(
                destination=name,
                origin=dict(
                    kind="git",
                    repository_id="repository-one",
                    commit=commit,
                    path=name,
                    sha256=sha((root / name).read_bytes()),
                ),
            )
            for name in sorted(paths)
        ],
    )
    closure["digest"] = closure_digest(closure)
    body = publication(runtime, dict(source_commit=commit, operation_id="unused"))
    del body["items"][0]["checkpoint_operation_id"]
    body["items"][0]["source"] = closure
    runtime.catalogue.publish(runtime.caller, body)
    return closure


def test_large_scene_closure_inheritance_and_checkpoint_publication(runtime):
    base = base_scene(runtime, extra=443)
    (runtime.root / ENTRY).write_bytes(b"Boardroom v2")
    row = dict(
        destination="scenes/externals/file-000.txt",
        origin=retain(runtime, data=b"Replacement external"),
    )
    body = request(
        runtime,
        entry_path=ENTRY,
        paths=[ENTRY],
        base_version=BASE_VERSION,
        exclude_paths=["scenes/old-display.txt"],
        retained_files=[row],
        required_runtime=base["required_runtime"],
    )
    tool = Tools(runtime.services, SimpleNamespace(), PROJECT)
    tool.caller = lambda *args, **kwargs: runtime.caller
    text = asyncio.run(
        tool.invoke(
            "checkpoint_sources",
            ToolInvocation(
                session_id="session-one",
                tool_call_id="checkpoint-one",
                tool_name="checkpoint_sources",
                arguments=body,
                agent_id=None,
            ),
        )
    ).text_result_for_llm
    receipt = validate("CheckpointReceipt", json.loads(text))
    closure = receipt["source_closure"]
    assert len(text.encode()) > 16_384
    for value in (receipt["operation_id"], receipt["source_commit"], closure["digest"]):
        assert value.encode() in text.encode()[:512]
    assert list(json.loads(text)["source_closure"]) == [
        "digest",
        "entry_path",
        "dependencies",
        "required_runtime",
        "files",
    ]
    assert text.index('"replayed"') < text.index('"files"')
    assert closure["digest"] == closure_digest(closure)
    by_path = {r["destination"]: r for r in closure["files"]}
    assert len(by_path) == 444 and "scenes/old-display.txt" not in by_path
    assert by_path[ENTRY]["origin"]["commit"] == receipt["source_commit"]
    assert by_path[ENTRY]["origin"]["sha256"] == sha(b"Boardroom v2")
    assert by_path[row["destination"]] == row
    assert (
        next(r for r in base["files"] if r["destination"].endswith("-001.txt"))
        == by_path["scenes/externals/file-001.txt"]
    )
    assert git(runtime.root, "status", "--porcelain") == b""
    assert runtime.sources.checkpoint(runtime.caller, body) == {**receipt, "replayed": True}
    ref = {**BASE_VERSION, "version_id": "boardroom-v2"}
    submission = publication(runtime, receipt, ref=ref, previous=BASE_VERSION["version_id"])
    assert authoring_validation(runtime, submission)["valid"]
    published, replayed = runtime.catalogue.publish(runtime.caller, submission)
    assert not replayed and published["versions"] == [ref]
    assert runtime.catalogue.publish(runtime.caller, submission) == (
        {**published, "replayed": True},
        True,
    )
    with runtime.store.transaction(write=False) as tx:
        version = tx.require("versions", ref["version_id"])
        assert validate("VersionRecord", version)["source"] == closure
        assert "checkpoint_operation_id" not in version
        assert tx.require("publications", submission["publication_id"])["submission"] == submission


@pytest.mark.parametrize(
    "case,code",
    [
        ("base_without_entry", "invalid_request"),
        ("exclude_without_entry", "invalid_request"),
        ("exclude_without_base", "invalid_request"),
        ("missing_base", "dependency_missing"),
        ("wrong_item", "scope_denied"),
        ("wrong_project", "scope_denied"),
        ("receipt_exclusion", "scope_denied"),
        ("secret_exclusion", "scope_denied"),
        ("duplicate_exclusion", "invalid_request"),
        ("excluded_path", "dependency_collision"),
        ("excluded_retained", "dependency_collision"),
        ("prefix_collision", "dependency_collision"),
        ("excluded_entry", "dependency_missing"),
    ],
)
def test_invalid_base_or_exclusions_fail_before_intent(runtime, case, code):
    base_scene(runtime)
    (runtime.root / "new.txt").write_text("Owned source")
    body = request(runtime, entry_path=ENTRY, paths=["new.txt"], base_version=BASE_VERSION)
    excluded = "scenes/old-display.txt"
    if case.endswith("without_entry"):
        del body["entry_path"]
        if case == "exclude_without_entry":
            body["exclude_paths"] = []
    elif case == "exclude_without_base":
        del body["base_version"]
        body["exclude_paths"] = []
    elif case in {"missing_base", "wrong_item"}:
        body["base_version"] = {
            **BASE_VERSION,
            ("item_id" if case == "wrong_item" else "version_id"): "wrong",
        }
    elif case == "wrong_project":
        with runtime.store.transaction() as tx:
            tx.connection.execute(
                "UPDATE records SET body=json_set(body,'$.project_id','wrong') WHERE namespace='versions'"
            )
    elif case == "duplicate_exclusion":
        body["exclude_paths"] = [excluded, excluded]
    elif case in {"receipt_exclusion", "secret_exclusion", "excluded_path", "excluded_entry"}:
        body["exclude_paths"] = [
            {
                "receipt_exclusion": ".receipt.json/child",
                "secret_exclusion": ".env",
                "excluded_path": "new.txt",
                "excluded_entry": ENTRY,
            }[case]
        ]
    else:
        body["retained_files"] = [
            dict(
                destination=excluded + ("/child" if case == "prefix_collision" else ""),
                origin=retain(runtime),
            )
        ]
        if case == "excluded_retained":
            body["exclude_paths"] = [excluded]
    with pytest.raises(DomainError) as error:
        runtime.sources.checkpoint(runtime.caller, body)
    assert error.value.code == code
    if case.endswith("without_entry"):
        assert str(error.value) == "entry_path is required when building a source closure"
    assert git(runtime.root, "rev-parse", "HEAD").decode().strip() == body["expected_head"]
    with runtime.store.transaction(write=False) as tx:
        assert not tx.all("checkpoint_intents")


@pytest.mark.parametrize("case", ["absent", "uncertain", "no_closure", "wrong_commit"])
def test_publication_rejects_an_unusable_checkpoint_receipt(runtime, case):
    write_plan(runtime)
    body = request(runtime, **({} if case == "no_closure" else {"entry_path": "plan.json"}))
    receipt = runtime.sources.checkpoint(runtime.caller, body)
    submission = publication(runtime, receipt)
    if case == "absent":
        submission["items"][0]["checkpoint_operation_id"] = "missing"
    elif case == "uncertain":
        with runtime.store.transaction() as tx:
            row = tx.require("checkpoint_intents", receipt["operation_id"])
            tx.put(
                "checkpoint_intents",
                receipt["operation_id"],
                {**row, "state": "outcome_uncertain"},
                expected_revision=tx.revision("checkpoint_intents", receipt["operation_id"]),
            )
    elif case == "wrong_commit":
        submission["items"][0]["source_commit"] = body["expected_head"]
    result = authoring_validation(runtime, submission)
    assert result["schema_valid"] and not result["semantic_valid"]
    assert result["issues"][0]["code"] == "source_conflict"
    assert result["issues"][0]["message"] == BAD_RECEIPT
    with pytest.raises(DomainError, match=BAD_RECEIPT) as error:
        runtime.catalogue.publish(runtime.caller, submission)
    assert error.value.code == "source_conflict" and error.value.status == 409
    with runtime.store.transaction(write=False) as tx:
        assert not tx.all("publications") and not tx.all("versions")


def test_published_item_source_options_are_mutually_exclusive(runtime):
    write_plan(runtime)
    receipt = runtime.sources.checkpoint(runtime.caller, request(runtime, entry_path="plan.json"))
    row = publication(runtime, receipt)["items"][0]
    validate("PublishedItem", row)
    assert result_text("checkpoint_sources", receipt) == canonical(receipt).decode()
    with pytest.raises(DomainError):
        validate("PublishedItem", {**row, "source": receipt["source_closure"]})
    del row["checkpoint_operation_id"]
    with pytest.raises(DomainError):
        validate("PublishedItem", row)
    validate("PublishedItem", {**row, "source": receipt["source_closure"]})


def test_large_checkpoint_response_with_max_length_identifiers(runtime):
    write_plan(runtime)
    receipt = runtime.sources.checkpoint(runtime.caller, request(runtime, entry_path="plan.json"))
    receipt.update(
        operation_id="o" * 128, project_id="p" * 128, source_commit="c" * 64, parent_commit="a" * 64
    )
    receipt["source_closure"]["files"] *= 300
    receipt["source_closure"]["digest"] = closure_digest(receipt["source_closure"])
    text = result_text("checkpoint_sources", receipt)
    for value in (
        receipt["operation_id"],
        receipt["source_commit"],
        receipt["source_closure"]["digest"],
    ):
        assert value.encode() in text.encode()[:512]
    assert validate("CheckpointReceipt", json.loads(text)) == receipt


def checkpoint_scene_v1(runtime, *, retained=False):
    root = runtime.root
    lfs_entry(runtime, b"Boardroom v1")
    git(root, "lfs", "track", "*.png")
    git(root, "add", ".gitattributes")
    git(root, "commit", "-m", "Track LFS files")
    (root / "texture.png").write_bytes(b"Texture v1 fixture")
    base_request = request(
        runtime, operation_id="checkpoint-v1", entry_path=ENTRY, paths=[ENTRY, "texture.png"]
    )
    if retained:
        base_request["retained_files"] = [
            dict(destination="remote-texture.dat", origin=retain(runtime))
        ]
    receipt = runtime.sources.checkpoint(runtime.caller, base_request)
    runtime.catalogue.publish(runtime.caller, publication(runtime, receipt))
    return receipt


def test_external_only_revision_repins_unchanged_lfs_entry_and_publishes(runtime):
    v1 = checkpoint_scene_v1(runtime)
    (runtime.root / "texture.png").write_bytes(b"Texture v2 fixture")
    body = request(runtime, entry_path=ENTRY, paths=["texture.png"], base_version=BASE_VERSION)
    v2 = runtime.sources.checkpoint(runtime.caller, body)
    source = v2["source_closure"]
    v1_entry = next(row for row in v1["source_closure"]["files"] if row["destination"] == ENTRY)
    v2_entry = next(row for row in source["files"] if row["destination"] == ENTRY)
    assert v2_entry == {**v1_entry, "origin": {**v1_entry["origin"], "commit": v2["source_commit"]}}
    assert v1["source_commit"] != v2["source_commit"]
    assert source["digest"] == closure_digest(source)
    assert runtime.sources.checkpoint(runtime.caller, body) == {**v2, "replayed": True}
    ref = {**BASE_VERSION, "version_id": "boardroom-v2"}
    submit = publication(runtime, v2, ref=ref, previous=BASE_VERSION["version_id"])
    assert authoring_validation(runtime, submit)["valid"]
    receipt, replayed = runtime.catalogue.publish(runtime.caller, submit)
    assert not replayed and receipt["versions"] == [ref]
    snapshot = runtime.sources.resolve(PROJECT, source)
    assert (snapshot / ENTRY).read_bytes() == b"Boardroom v1"
    assert (snapshot / "texture.png").read_bytes() == b"Texture v2 fixture"


def test_changed_inherited_entry_rejected_before_intent_or_git_objects(runtime):
    checkpoint_scene_v1(runtime)
    root = runtime.root
    (root / ENTRY).write_bytes(b"Different boardroom")
    git(root, "add", ENTRY)
    git(root, "commit", "-m", "Update scene separately")
    (root / "texture.png").write_bytes(b"Texture v2 fixture")
    body = request(runtime, entry_path=ENTRY, paths=["texture.png"], base_version=BASE_VERSION)
    refs = git(root, "show-ref")
    objects = git(root, "count-objects", "-v")
    with pytest.raises(
        DomainError, match="Inherited entry_path does not match HEAD; include entry_path in paths"
    ) as error:
        runtime.sources.checkpoint(runtime.caller, body)
    assert error.value.code == "source_conflict" and error.value.status == 409
    assert git(root, "show-ref") == refs
    assert git(root, "count-objects", "-v") == objects
    with runtime.store.transaction(write=False) as tx:
        assert tx.get("checkpoint_intents", body["operation_id"], required=False) is None


@pytest.mark.parametrize("damage", ["missing_record", "missing_bytes", "corrupt_bytes"])
def test_inherited_retained_source_is_verified_before_checkpoint(runtime, damage):
    v1 = checkpoint_scene_v1(runtime, retained=True)
    origin = next(
        row["origin"]
        for row in v1["source_closure"]["files"]
        if row["origin"]["kind"] == "retained"
    )
    blob = runtime.root / f".proscenium/retained/{origin['sha256']}/content"
    if damage == "missing_record":
        with runtime.store.transaction() as tx:
            tx.connection.execute(
                "DELETE FROM records WHERE namespace='retained_sources' AND id=?",
                (origin["artifact_id"],),
            )
    elif damage == "missing_bytes":
        blob.unlink()
    else:
        blob.write_bytes(b"Corrupt content")
    body = request(runtime, entry_path=ENTRY, paths=["texture.png"], base_version=BASE_VERSION)
    with pytest.raises(DomainError) as error:
        runtime.sources.checkpoint(runtime.caller, body)
    assert error.value.code == "retained_source_missing"
    with runtime.store.transaction(write=False) as tx:
        assert tx.get("checkpoint_intents", body["operation_id"], required=False) is None
    assert git(runtime.root, "rev-parse", "HEAD").decode().strip() == body["expected_head"]


@pytest.mark.parametrize("change", ["removed_base", "different_work"])
def test_modern_checkpoint_cannot_fall_back_to_legacy_replay(runtime, change):
    checkpoint_scene_v1(runtime)
    body = request(
        runtime, entry_path=ENTRY, paths=[ENTRY, "texture.png"], base_version=BASE_VERSION
    )
    runtime.sources.checkpoint(runtime.caller, body)
    caller = runtime.caller
    if change == "removed_base":
        del body["base_version"]
    else:
        caller = dataclasses.replace(caller, work_id="different-work")
    with pytest.raises(DomainError) as error:
        runtime.sources.checkpoint(caller, body)
    assert error.value.code == "idempotency_conflict"


def test_index_sync_defers_to_prepared_ref_writer_and_cleans_its_locks(runtime):
    write_plan(runtime)
    root = runtime.root
    index = root / ".git/index"
    index_lock = index.with_suffix(".lock")
    index_lock.touch()
    before = index.read_bytes()
    body = request(runtime, entry_path="plan.json")
    receipt = runtime.sources.checkpoint(runtime.caller, body)
    index_lock.unlink()
    branch_lock = root / ".git/refs/heads/main.lock"
    with prepared_branch(root, "refs/heads/main", receipt["source_commit"]) as held:
        assert held and branch_lock.exists()
        assert runtime.sources.checkpoint(runtime.caller, body) == {**receipt, "replayed": True}
        assert index.read_bytes() == before
        assert not index_lock.exists() and branch_lock.exists()
        assert not list(index.parent.glob("proscenium-index-*"))
    assert not branch_lock.exists()
    runtime.sources.checkpoint(runtime.caller, body)
    assert not index_lock.exists() and not branch_lock.exists()
    assert git(root, "status", "--porcelain") == b""
