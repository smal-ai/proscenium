"""Hermetic request identity, source verification and trusted-output contract checks."""

import copy
import hashlib
import subprocess
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import yaml
from copilot import CopilotClient
from proscenium.api.errors import DomainError
from proscenium.auth import Caller
from proscenium.configuration import Settings
from proscenium.contracts import SCHEMA, canonical, validate
from proscenium.copilot.context import Contexts
from proscenium.copilot.outcomes import (
    NAMESPACE,
    artifact_attributions,
    get_tool_outcome,
    record_tool_outcome,
)
from proscenium.db import Store
from proscenium.providers.targets import admit_target, resolve_target
from proscenium.sources.read_only import read_authored_json, read_closure_files
from proscenium.sources.resolver import closure_digest

PROJECT, WORK, TASK = "project-one", "work-one", "task-one"
STAMP = "2026-11-15T10:00:00+02:00"
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Request targeting tests must remain offline")

    async def blocked_async(*args, **kwargs):
        blocked()

    monkeypatch.setattr(CopilotClient, "start", blocked_async)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", blocked_async)


def context(identity="context-one", **changes):
    return {
        **dict(
            schema_version="1.0",
            context_id=identity,
            project_id=PROJECT,
            captured_at=STAMP,
            item_id=None,
            version_id=None,
            view_mode="none",
            target=None,
            camera=None,
            range=None,
            selected_objects=[],
            attachments=[],
            annotations=[],
            approved_findings=[],
        ),
        **changes,
    }


def plan(project_id=PROJECT):
    return dict(
        schema_version="1.0",
        project_id=project_id,
        plan_id="plan-one",
        brief_artifact_ids=[],
        asset_dependencies=[],
        unresolved=[],
        shots=[
            dict(
                shot_id="shot-one",
                intent="A synthetic shot",
                camera_intent="Static",
                motion_beats=[],
                scene_version=None,
                source_range=None,
                fps=None,
                frames=[
                    dict(
                        frame_id="frame-one",
                        role="start",
                        composition="Wide",
                        artifact_id=None,
                        state="missing",
                    )
                ],
            )
        ],
    )


def request_record(identity="queue-one", context_id="context-one", kind="queue"):
    submission = dict(
        schema_version="1.0",
        project_id=PROJECT,
        request_id=identity,
        type=kind,
        text="Synthetic request",
        context_id=context_id,
    )
    if kind == "steer":
        submission["target_work_id"] = WORK
    return dict(
        schema_version="1.0",
        project_id=PROJECT,
        request_id=identity,
        submission=submission,
        accepted_at=STAMP,
        effective_profile_id=None,
        generation_task_id=TASK,
        receipt=dict(
            schema_version="1.0",
            project_id=PROJECT,
            request_id=identity,
            type=kind,
            state="acknowledged",
            work_id=WORK,
            native_message_id=None,
            state_revision=0,
            replayed=False,
        ),
    )


@pytest.fixture
def runtime(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    store = Store(root / ".proscenium/state.sqlite3", PROJECT)
    project = dict(
        schema_version="1.0",
        project_id=PROJECT,
        repository_id="repository-one",
        name="Offline fixture",
        path=str(root),
        native_session_id="session-one",
        state="active",
        state_revision=0,
    )
    services = SimpleNamespace(
        registry=SimpleNamespace(
            project_store=lambda project_id: store, get=lambda project_id: project
        ),
        settings=Settings(data_directory=tmp_path / "machine"),
    )
    work = dict(
        schema_version="1.0",
        project_id=PROJECT,
        work_id=WORK,
        originating_request_id="queue-one",
        directive_request_ids=["steer-one"],
        state="running",
        state_revision=0,
        native_session_id="session-one",
        job_ids=[],
        generation_task_id=TASK,
    )
    with store.transaction() as tx:
        tx.put("work", WORK, work, schema="WorkRecord")
        tx.put("copilot.effective", "queue-one", {"generation_attempt_limit": 2})
        tx.put("copilot.effective", "steer-one", {"generation_attempt_limit": 99})
    return services, store, root, work


def add_version(
    runtime,
    item_id="asset-one",
    version_id="version-old",
    kind="asset",
    document=None,
    data=None,
    project_id=PROJECT,
):
    _, store, root, _ = runtime
    data = data if data is not None else canonical(document if document is not None else {})
    digest = hashlib.sha256(data).hexdigest()
    path = root / f".proscenium/retained/{digest}/content"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    closure = dict(
        entry_path="source.json",
        files=[
            dict(
                destination="source.json",
                origin=dict(
                    kind="retained",
                    artifact_id="retained-" + digest,
                    sha256=digest,
                ),
            )
        ],
        dependencies=[],
        required_runtime=dict(application="json", version="1", features=[]),
        digest="0" * 64,
    )
    closure["digest"] = closure_digest(closure)
    version = dict(
        schema_version="1.0",
        project_id=project_id,
        item_id=item_id,
        version_id=version_id,
        kind=kind,
        name="Synthetic version",
        publication_id="publication-one",
        source_commit="a" * 40,
        source=closure,
        artifact_ids=[],
        preview_requests=[],
        preview_job_ids=[],
        created_at=STAMP,
        actor=dict(kind="tool", id="fixture"),
    )
    with store.transaction() as tx:
        if not tx.get("retained_sources", "retained-" + digest, required=False):
            tx.put("retained_sources", "retained-" + digest, {"sha256": digest})
        # Cross-project records are deliberately injected below the storage scope gate.
        tx.connection.execute(
            "INSERT INTO records VALUES (?,?,0,?)",
            ("versions", version_id, canonical(version).decode()),
        )
        item = tx.get("items", item_id, required=False)
        if not item:
            item = dict(
                schema_version="1.0",
                project_id=project_id,
                item_id=item_id,
                kind=kind,
                name="Synthetic item",
                current_version_id=version_id,
                selected_version_id=None,
                state_revision=0,
            )
            tx.connection.execute(
                "INSERT INTO records VALUES (?,?,0,?)", ("items", item_id, canonical(item).decode())
            )
        else:
            item.update(current_version_id=version_id, state_revision=item["state_revision"] + 1)
            tx.put("items", item_id, item, expected_revision=tx.revision("items", item_id))
    return version


def bind_request(runtime, body, identity="queue-one", kind="queue", invocation=None):
    services, store, _, _ = runtime
    Contexts(services).capture(body)
    with store.transaction() as tx:
        record = request_record(identity, body["context_id"], kind)
        tx.put("requests", identity, record, schema="RequestRecord")
        if invocation:
            tx.put(
                "invocations",
                invocation,
                dict(invocation_id=invocation, work_id=WORK, request_id=identity),
            )
    return record


def generation_request(target="asset-one", slot=None):
    return dict(
        project_id=PROJECT,
        generation_task_id=TASK,
        target_id=target,
        settings={} if slot is None else {"target_slot": slot},
    )


def caller(invocation=None):
    return Caller(
        channel="tool",
        subject_id="fixture",
        project_id=PROJECT,
        work_id=WORK,
        invocation_id=invocation,
    )


def assert_error(code, action):
    with pytest.raises(DomainError) as error:
        action()
    assert error.value.code == code
    return error.value


def test_legacy_records_and_capture_fingerprints_are_unchanged(runtime):
    services, store, _, _ = runtime
    body = context()
    original = canonical(body)
    assert validate("ContextSnapshot", body) == body
    contexts = Contexts(services)
    assert contexts.capture(body) == (body, False)
    assert contexts.capture(copy.deepcopy(body)) == (body, True)
    with store.transaction(write=False) as tx:
        assert canonical(tx.require("contexts", body["context_id"])) == original
        fingerprint = tx.connection.execute(
            "SELECT fingerprint FROM operations WHERE scope='context.capture'"
        ).fetchone()[0]
        assert fingerprint == hashlib.sha256(original).hexdigest()
    snapshot = dict(
        schema_version="1.0",
        project=services.registry.get(PROJECT),
        settings=dict(generation_attempt_limit=3, default_profile_id=None, state_revision=0),
        event_sequence=0,
        active_work=None,
        held_requests=[],
        items=[],
        jobs=[],
        pending_decisions=[],
    )
    assert validate("ProjectSnapshot", snapshot) == snapshot
    activity = dict(
        invocation_id="tool-one",
        name="bash",
        state="completed",
        summary="",
        artifact_ids=[],
        model_id=None,
    )
    assert validate("ActivityEventData", activity) == activity
    assert "execution" not in snapshot and "exit_code" not in activity
    assert_error(
        "idempotency_conflict", lambda: contexts.capture({**body, "execution_scope": "default"})
    )


@pytest.mark.parametrize(
    "target",
    [
        {"kind": "project"},
        {"kind": "item", "version": {"item_id": "asset-one", "version_id": "version-old"}},
        {
            "kind": "shot",
            "storyboard_version": {"item_id": "board-one", "version_id": "board-old"},
            "shot_id": "shot-one",
        },
        {"kind": "new_item", "item_kind": "asset", "intent_id": "intent-one", "name": "New asset"},
    ],
)
def test_request_target_union_is_closed(target):
    assert validate("RequestTarget", target) == target
    assert_error("invalid_request", lambda: validate("RequestTarget", {**target, "unknown": True}))


@pytest.mark.parametrize(
    "target",
    [
        {"kind": "unknown"},
        {"kind": "item"},
        {"kind": "shot", "shot_id": "shot-one"},
        {"kind": "new_item", "item_kind": "sequence", "intent_id": "intent-one"},
        {"kind": "new_item", "item_kind": "asset", "intent_id": "intent-one", "name": ""},
        {"kind": "new_item", "item_kind": "asset", "intent_id": "intent-one", "name": "a" * 201},
    ],
)
def test_invalid_request_targets(target):
    assert_error("invalid_request", lambda: validate("RequestTarget", target))


@pytest.mark.parametrize("kind", ["project", "new_item"])
def test_non_published_targets_clear_selection(runtime, kind):
    services, _, root, _ = runtime
    target = {"kind": kind}
    if kind == "new_item":
        target.update(item_kind="storyboard", intent_id="intent-one")
    body = context(request_target=target, execution_scope="planning_only")
    assert Contexts(services).capture(body) == (body, False)
    assert_error(
        "source_conflict",
        lambda: Contexts(services).capture(
            {**body, "context_id": "bad-context", "item_id": "asset-one"}
        ),
    )
    assert not (root / ".proscenium/snapshots").exists()


@pytest.mark.parametrize("mode", ["item", "reference"])
@pytest.mark.parametrize(
    ("problem", "code"),
    [
        ("none", None),
        ("missing_version", "not_found"),
        ("wrong_item", "source_conflict"),
        ("cross_project", "scope_denied"),
    ],
)
def test_pinned_version_validation(runtime, mode, problem, code):
    services, _, root, _ = runtime
    add_version(runtime, project_id="other-project" if problem == "cross_project" else PROJECT)
    if problem != "cross_project":
        add_version(runtime, version_id="version-new")
    add_version(runtime, item_id="other-item", version_id="other-version")
    ref = dict(
        item_id="other-item" if problem == "wrong_item" else "asset-one",
        version_id="absent" if problem == "missing_version" else "version-old",
    )
    body = context(
        **(
            {"request_target": {"kind": "item", "version": ref}}
            if mode == "item"
            else {"reference_versions": [ref]}
        )
    )
    if code:
        assert_error(code, lambda: Contexts(services).capture(body))
    else:
        assert Contexts(services).capture(body) == (body, False)
    assert not (root / ".proscenium/snapshots").exists()


@pytest.mark.parametrize(
    ("problem", "code"),
    [
        ("none", None),
        ("missing_shot", "not_found"),
        ("wrong_kind", "source_conflict"),
        ("cross_project", "scope_denied"),
        ("plan_project", "scope_denied"),
        ("html", "source_unavailable"),
        ("array", "source_unavailable"),
        ("invalid_plan", "invalid_request"),
        ("selection_conflict", "source_conflict"),
    ],
)
def test_shot_targets_read_only(runtime, problem, code):
    services, _, root, _ = runtime
    data = (
        b"<html>not a storyboard</html>"
        if problem == "html"
        else b"[]"
        if problem == "array"
        else None
    )
    document = (
        {}
        if problem == "invalid_plan"
        else plan("other-project" if problem == "plan_project" else PROJECT)
    )
    add_version(
        runtime,
        item_id="board-one",
        version_id="board-old",
        kind="asset" if problem == "wrong_kind" else "storyboard",
        data=data,
        document=document,
        project_id="other-project" if problem == "cross_project" else PROJECT,
    )
    body = context(
        request_target=dict(
            kind="shot",
            storyboard_version=dict(item_id="board-one", version_id="board-old"),
            shot_id="missing" if problem == "missing_shot" else "shot-one",
        )
    )
    if problem == "selection_conflict":
        body.update(item_id="board-one", version_id="another-version")
    if code:
        error = assert_error(code, lambda: Contexts(services).capture(body))
        if code == "source_unavailable":
            assert error.message == "Storyboard source must be AuthoredShotPlan JSON"
    else:
        assert Contexts(services).capture(body) == (body, False)
        assert Contexts(services).capture(body) == (body, True)
    assert not (root / ".proscenium/snapshots").exists()


def test_item_target_selection_and_execution_scope_validation(runtime):
    services, _, _, _ = runtime
    add_version(runtime)
    body = context(
        request_target=dict(
            kind="item", version=dict(item_id="asset-one", version_id="version-old")
        )
    )
    assert_error(
        "source_conflict",
        lambda: Contexts(services).capture(
            {**body, "item_id": "asset-one", "version_id": "different"}
        ),
    )
    assert_error(
        "invalid_request",
        lambda: Contexts(services).capture({**body, "execution_scope": "inference"}),
    )
    assert_error(
        "invalid_request",
        lambda: Contexts(services).capture(
            {**body, "reference_versions": [body["request_target"]["version"]] * 2}
        ),
    )


@pytest.mark.parametrize("modern", [False, True])
def test_native_payload_defaults_and_explicit_context(runtime, modern):
    services, store, _, _ = runtime
    add_version(runtime)
    references = [dict(item_id="asset-one", version_id="version-old")] if modern else []
    changes = (
        dict(
            request_target={"kind": "project"},
            reference_versions=references,
            execution_scope="planning_only",
        )
        if modern
        else {}
    )
    body = context(**changes)
    record = bind_request(runtime, body)
    with store.transaction(write=False) as tx:
        payload = Contexts(services).payload(tx, record)
        assert payload["request_target"] == changes.get("request_target")
        assert payload["reference_versions"] == references
        assert payload["execution_scope"] == ("planning_only" if modern else "default")
        assert tx.require("contexts", body["context_id"]) == body


@pytest.mark.parametrize("pin", ["request_target", "reference_versions", "selection"])
def test_generation_keeps_pinned_historical_asset(runtime, pin):
    services, _, root, _ = runtime
    add_version(runtime)
    add_version(runtime, version_id="version-new")
    ref = dict(item_id="asset-one", version_id="version-old")
    changes = (
        {"request_target": {"kind": "item", "version": ref}}
        if pin == "request_target"
        else {"reference_versions": [ref]}
        if pin == "reference_versions"
        else ref
    )
    bind_request(runtime, {**context(), **changes})
    resolved = resolve_target(services, caller(), generation_request())
    assert resolved["target"]["version"] == ref
    assert resolved["item"]["current_version_id"] == "version-new"
    assert not (root / ".proscenium/snapshots").exists()


@pytest.mark.parametrize("pin", ["request_target", "reference_versions"])
def test_steer_invocation_uses_latest_context_not_latest_budget(runtime, pin):
    services, store, _, work = runtime
    add_version(runtime)
    add_version(runtime, version_id="version-new")
    queue = {**context(), "item_id": "asset-one", "version_id": "version-old"}
    bind_request(runtime, queue)
    ref = dict(item_id="asset-one", version_id="version-new")
    changes = (
        {"request_target": {"kind": "item", "version": ref}}
        if pin == "request_target"
        else {"reference_versions": [ref]}
    )
    bind_request(runtime, context("steer-context", **changes), "steer-one", "steer", "steer-tool")
    resolved = resolve_target(services, caller("steer-tool"), generation_request())
    assert resolved["target"]["version"] == ref
    assert resolved["context"]["context_id"] == "steer-context"
    with store.transaction() as tx:
        policy = admit_target(tx, work, resolved)
    assert policy == dict(task_id=TASK, limit=2, targets=["asset-one"])
    assert_error(
        "scope_denied",
        lambda: resolve_target(
            services,
            caller("steer-tool"),
            {**generation_request(), "generation_task_id": "new-budget"},
        ),
    )


def test_missing_invocation_binding_never_falls_back_to_queue(runtime):
    services, store, _, _ = runtime
    add_version(runtime)
    bind_request(runtime, context())
    with store.transaction() as tx:
        tx.put("invocations", "broken-tool", dict(work_id=WORK, request_id="missing"))
    assert_error(
        "not_found", lambda: resolve_target(services, caller("broken-tool"), generation_request())
    )
    assert (
        resolve_target(services, caller("unrecorded-tool"), generation_request())["context"][
            "context_id"
        ]
        == "context-one"
    )


def test_conflicting_reference_pins_and_unpinned_head(runtime):
    services, _, _, _ = runtime
    add_version(runtime)
    add_version(runtime, version_id="version-new")
    bind_request(
        runtime,
        context(
            reference_versions=[
                dict(item_id="asset-one", version_id=version)
                for version in ("version-old", "version-new")
            ]
        ),
    )
    assert_error(
        "source_conflict", lambda: resolve_target(services, caller(), generation_request())
    )


def test_unpinned_item_uses_current_head(runtime):
    services, _, _, _ = runtime
    add_version(runtime)
    add_version(runtime, version_id="version-new")
    bind_request(runtime, context(request_target={"kind": "project"}))
    assert (
        resolve_target(services, caller(), generation_request())["target"]["version"]["version_id"]
        == "version-new"
    )


@pytest.mark.parametrize("target", [TASK, "intent-one"])
def test_new_item_standalone_targets(runtime, target):
    services, _, root, _ = runtime
    bind_request(
        runtime,
        context(request_target=dict(kind="new_item", item_kind="asset", intent_id="intent-one")),
        invocation="new-tool",
    )
    resolved = resolve_target(services, caller("new-tool"), generation_request(target))
    assert resolved["target"] == dict(target_id=target, version=None, shot_id=None)
    assert resolved["context"]["request_target"]["intent_id"] == "intent-one"
    assert_error(
        "scope_denied",
        lambda: resolve_target(services, caller("new-tool"), generation_request("renamed-budget")),
    )
    assert not (root / ".proscenium/snapshots").exists()


def test_policy_standalone_target_remains_supported(runtime):
    services, store, _, _ = runtime
    with store.transaction() as tx:
        tx.put("providers.tasks", TASK, dict(task_id=TASK, targets=["existing-concept"], limit=2))
    assert (
        resolve_target(services, caller(), generation_request("existing-concept"))["target"][
            "target_id"
        ]
        == "existing-concept"
    )


def test_storyboard_slot_resolves_without_snapshots(runtime):
    services, _, root, _ = runtime
    add_version(
        runtime, item_id="board-one", version_id="board-old", kind="storyboard", document=plan()
    )
    slot = dict(
        kind="storyboard_frame",
        storyboard_version=dict(item_id="board-one", version_id="board-old"),
        shot_id="shot-one",
        frame_id="frame-one",
    )
    resolved = resolve_target(services, caller(), generation_request(slot=slot))
    assert resolved["target"]["shot_id"] == "shot-one"
    assert resolved["target"]["version"] == slot["storyboard_version"]
    assert not (root / ".proscenium/snapshots").exists()


def test_assembly_slot_resolves_without_snapshots(runtime):
    services, _, root, _ = runtime
    document = dict(
        schema_version="1.0",
        project_id=PROJECT,
        sequence_version_id="sequence-old",
        fps=dict(numerator=24, denominator=1),
        frame_count=24,
        silent=True,
        entries=[
            dict(
                entry_id="entry-one",
                shot_id="shot-one",
                version_id="scene-old",
                job_id="render-one",
                source_frames=dict(start=1, end=24),
                sequence_frames=dict(start=1, end=24),
                transform="identity",
                mapping_artifact_id=None,
            )
        ],
    )
    add_version(
        runtime,
        item_id="sequence-one",
        version_id="sequence-old",
        kind="sequence",
        document=document,
    )
    slot = dict(
        kind="assembly_shot",
        sequence_version=dict(item_id="sequence-one", version_id="sequence-old"),
        entry_id="entry-one",
    )
    resolved = resolve_target(services, caller(), generation_request(slot=slot))
    assert resolved["target"]["shot_id"] == "shot-one"
    assert resolved["target"]["version"] == slot["sequence_version"]
    assert not (root / ".proscenium/snapshots").exists()


def test_shot_request_target_keeps_historical_storyboard_pin(runtime):
    services, _, root, _ = runtime
    add_version(
        runtime, item_id="board-one", version_id="board-old", kind="storyboard", document=plan()
    )
    add_version(
        runtime, item_id="board-one", version_id="board-new", kind="storyboard", document=plan()
    )
    ref = dict(item_id="board-one", version_id="board-old")
    bind_request(
        runtime,
        context(request_target=dict(kind="shot", storyboard_version=ref, shot_id="shot-one")),
    )
    resolved = resolve_target(services, caller(), generation_request("board-one"))
    assert resolved["target"]["version"] == ref
    assert not (root / ".proscenium/snapshots").exists()


def test_explicit_slot_cannot_override_request_version_pin(runtime):
    services, _, _, _ = runtime
    add_version(
        runtime, item_id="board-one", version_id="board-old", kind="storyboard", document=plan()
    )
    add_version(
        runtime, item_id="board-one", version_id="board-new", kind="storyboard", document=plan()
    )
    bind_request(
        runtime, context(reference_versions=[dict(item_id="board-one", version_id="board-old")])
    )
    slot = dict(
        kind="storyboard_frame",
        storyboard_version=dict(item_id="board-one", version_id="board-new"),
        shot_id="shot-one",
        frame_id="frame-one",
    )
    assert_error(
        "source_conflict", lambda: resolve_target(services, caller(), generation_request(slot=slot))
    )


@pytest.mark.parametrize("namespace", ["versions", "contexts"])
def test_admission_rechecks_exact_resolved_records(runtime, namespace):
    services, store, _, work = runtime
    add_version(runtime)
    bind_request(runtime, context())
    resolved = resolve_target(services, caller(), generation_request())
    identity = "version-old" if namespace == "versions" else "context-one"
    with store.transaction() as tx:
        record = tx.require(namespace, identity)
        changed = (
            {**record, "name": "Changed"}
            if namespace == "versions"
            else {**record, "execution_scope": "planning_only"}
        )
        tx.put(namespace, identity, changed, expected_revision=tx.revision(namespace, identity))
        assert_error("source_conflict", lambda: admit_target(tx, work, resolved))


@pytest.mark.parametrize(
    ("problem", "code"),
    [
        ("digest", "hash_mismatch"),
        ("missing_entry", "dependency_missing"),
        ("duplicate", "dependency_collision"),
        ("prefix_collision", "dependency_collision"),
        ("secret", "scope_denied"),
        ("symlink", "scope_denied"),
        ("retained_missing", "retained_source_missing"),
        ("retained_corrupt", "hash_mismatch"),
        ("dependency_missing", "dependency_missing"),
        ("dependency_wrong_item", "source_conflict"),
        ("dependency_cross_project", "scope_denied"),
    ],
)
def test_closure_verification_rejects_unsafe_sources(runtime, problem, code):
    services, _, root, _ = runtime
    version = add_version(runtime)
    closure = copy.deepcopy(version["source"])
    if problem == "digest":
        closure["digest"] = "f" * 64
    elif problem == "missing_entry":
        closure["entry_path"] = "absent.json"
    elif problem in {"duplicate", "prefix_collision"}:
        row = copy.deepcopy(closure["files"][0])
        if problem == "prefix_collision":
            row["destination"] += "/nested.json"
        closure["files"].append(row)
    elif problem == "secret":
        closure["entry_path"] = closure["files"][0]["destination"] = ".env"
    elif problem == "symlink":
        (root / "source.json").symlink_to(root / "project.json")
    elif problem == "retained_missing":
        closure["files"][0]["origin"]["artifact_id"] = "unknown-retained"
    elif problem == "retained_corrupt":
        digest = closure["files"][0]["origin"]["sha256"]
        (root / f".proscenium/retained/{digest}/content").write_bytes(b"corrupt")
    else:
        ref = dict(item_id="asset-one", version_id="missing")
        if problem == "dependency_wrong_item":
            ref.update(item_id="different-item", version_id="version-old")
        elif problem == "dependency_cross_project":
            add_version(
                runtime,
                item_id="foreign-item",
                version_id="foreign-version",
                project_id="foreign-project",
            )
            ref.update(item_id="foreign-item", version_id="foreign-version")
        closure["dependencies"] = [ref]
    if problem != "digest":
        closure["digest"] = closure_digest(closure)
    assert_error(code, lambda: read_closure_files(services, PROJECT, closure))
    assert not (root / ".proscenium/snapshots").exists()


def test_batch_dependencies_and_json_objects(runtime):
    services, _, root, _ = runtime
    version = add_version(runtime, data=b"[1, 2]")
    closure = copy.deepcopy(version["source"])
    closure["dependencies"] = [dict(item_id="new-item", version_id="batch-version")]
    closure["digest"] = closure_digest(closure)
    batch = {
        "batch-version": dict(project_id=PROJECT, item_id="new-item", version_id="batch-version")
    }
    assert read_closure_files(services, PROJECT, closure, batch=batch) == {"source.json": b"[1, 2]"}
    error = assert_error(
        "source_unavailable", lambda: read_authored_json(services, PROJECT, version)
    )
    assert error.message == "Authored catalogue source is not JSON"
    assert not (root / ".proscenium/snapshots").exists()


def test_git_source_reads_only_local_immutable_blob(runtime):
    services, _, root, _ = runtime

    def git(*args):
        return (
            subprocess.run(
                ["git", "-c", "core.hooksPath=/dev/null", "-C", str(root), *args],
                check=True,
                capture_output=True,
            )
            .stdout.decode()
            .strip()
        )

    git("init")
    data = canonical(plan())
    (root / "plan.json").write_bytes(data)
    git("add", "plan.json")
    git(
        "-c",
        "user.name=Offline",
        "-c",
        "user.email=offline@example.invalid",
        "commit",
        "-m",
        "fixture",
    )
    commit = git("rev-parse", "HEAD")
    closure = dict(
        entry_path="plan.json",
        files=[
            dict(
                destination="plan.json",
                origin=dict(
                    kind="git",
                    repository_id="repository-one",
                    commit=commit,
                    path="plan.json",
                    sha256=hashlib.sha256(data).hexdigest(),
                ),
            )
        ],
        dependencies=[],
        required_runtime=dict(application="json", version="1", features=[]),
        digest="0" * 64,
    )
    closure["digest"] = closure_digest(closure)
    (root / "plan.json").write_bytes(b"mutable changes do not replace the pin")
    version = dict(project_id=PROJECT, kind="storyboard", source=closure)
    assert read_authored_json(services, PROJECT, version) == plan()
    assert not (root / ".proscenium/snapshots").exists()


def outcome(store, **changes):
    return record_tool_outcome(
        store,
        **{
            "invocation_id": "tool-one",
            "session_id": "session-one",
            "project_id": PROJECT,
            "work_id": WORK,
            "request_id": "steer-one",
            "tool_name": "register_tool_artifact",
            "status": "completed",
            "artifact_ids": ["output-one"],
            **changes,
        },
    )


def test_tool_outcome_store_transaction_replay_and_cas(runtime):
    _, store, _, _ = runtime
    first = outcome(store)
    assert outcome(store) == first
    with store.transaction() as tx:
        assert tx.revision(NAMESPACE, "tool-one") == 0
        assert get_tool_outcome(tx, "tool-one") == first
        assert get_tool_outcome(tx, "unknown") is None
        updated = outcome(
            tx,
            status="failed",
            error_code="invalid_request",
            phase="schema",
            safe_summary="Invalid authored document",
            issues=[dict(phase="schema", code="required", path="/shots", message="Missing field")],
        )
        assert tx.revision(NAMESPACE, "tool-one") == 1
        assert (
            outcome(
                tx,
                **{
                    key: updated[key]
                    for key in ("status", "error_code", "phase", "safe_summary", "issues")
                },
            )
            == updated
        )
        assert tx.revision(NAMESPACE, "tool-one") == 1
    assert_error("idempotency_conflict", lambda: outcome(store, request_id="another-request"))


def test_tool_outcomes_roll_back_with_callers_transaction(runtime):
    _, store, _, _ = runtime
    with pytest.raises(RuntimeError):
        with store.transaction() as tx:
            outcome(tx)
            raise RuntimeError("Caller aborted")
    with store.transaction(write=False) as tx:
        assert get_tool_outcome(tx, "tool-one") is None


def test_tool_outcome_deduplicates_arrays_and_preserves_project_scope(runtime):
    _, store, _, _ = runtime
    ref = dict(item_id="asset-one", version_id="version-old")
    first = outcome(
        store,
        artifact_ids=("output-one", "output-one"),
        published_versions=(ref, ref),
        job_ids=("job-one", "job-one"),
    )
    assert first["artifact_ids"] == ["output-one"]
    assert first["published_versions"] == [ref]
    assert first["job_ids"] == ["job-one"]
    assert first == outcome(store, published_versions=[ref], job_ids=["job-one"])
    assert_error(
        "scope_denied",
        lambda: outcome(store, invocation_id="foreign-tool", project_id="foreign-project"),
    )


def test_artifact_attributions_use_only_recorded_associations(runtime):
    _, store, _, _ = runtime
    with store.transaction() as tx:
        for identity in ("output-one", "legacy-output", "published-output", "job-output"):
            tx.put(
                "artifacts",
                identity,
                dict(
                    schema_version="1.0",
                    project_id=PROJECT,
                    artifact_id=identity,
                    kind="document",
                    purpose="metadata",
                    sha256="a" * 64,
                    bytes=1,
                    media_type="application/json",
                    state="ready",
                    derived_from=[],
                ),
                schema="Artifact",
            )
        outcome(tx)
        version = dict(
            project_id=PROJECT,
            item_id="asset-one",
            version_id="version-old",
            publication_id="publication-one",
            artifact_ids=["published-output"],
        )
        tx.put("versions", "version-old", version)
        tx.put(
            "publications",
            "publication-one",
            dict(
                request_id="queue-one",
                work_id=WORK,
                invocation_id="publish-tool",
                submission=dict(
                    publication_id="publication-one",
                    items=[
                        dict(
                            item_id="asset-one",
                            version_id="version-old",
                            artifact_ids=["published-output"],
                        )
                    ],
                ),
            ),
        )
        tx.put(
            "jobs",
            "job-one",
            dict(
                project_id=PROJECT,
                job_id="job-one",
                work_id=WORK,
                artifact_ids=["job-output"],
                target=dict(version=dict(item_id="asset-one", version_id="version-old")),
            ),
        )
        rows = artifact_attributions(tx)
        assert rows["legacy-output"] == dict(
            attribution_status="unrecorded",
            request_id=None,
            work_id=None,
            invocation_id=None,
            job_id=None,
            published_versions=[],
        )
        assert rows["output-one"]["attribution_status"] == "verified"
        assert rows["output-one"]["request_id"] == "steer-one"
        assert rows["published-output"]["invocation_id"] == "publish-tool"
        assert rows["published-output"]["published_versions"] == [
            dict(item_id="asset-one", version_id="version-old")
        ]
        assert rows["job-output"]["job_id"] == "job-one"
        assert rows["job-output"]["request_id"] is None
        assert rows == artifact_attributions(tx)


def test_validation_envelope_requires_exactly_one_source():
    base = dict(schema_version="1.0", project_id=PROJECT, document_kind="authored_shot_plan")
    for source in ({"path": "plan.json"}, {"document": {"invalid_authored_field": [1]}}):
        validate("AuthoredDocumentValidationRequest", {**base, **source})
    for source in ({}, {"path": "plan.json", "document": {}}, {"document": []}):
        assert_error(
            "invalid_request",
            lambda: validate("AuthoredDocumentValidationRequest", {**base, **source}),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("prompt", ""),
        ("prompt", "x" * 8001),
        ("seed", "-1"),
        ("seed", 42),
        ("width", 63),
        ("height", 4097),
        ("steps", 0),
        ("steps", 51),
    ],
)
def test_image_preparation_contract_bounds(field, value):
    body = dict(
        schema_version="1.0",
        operation_id="prepare-one",
        project_id=PROJECT,
        generation_task_id=TASK,
        prompt="A synthetic image",
        seed="42",
    )
    validate("ImageGenerationPrepare", body)
    assert_error(
        "invalid_request", lambda: validate("ImageGenerationPrepare", {**body, field: value})
    )


def test_additive_schema_required_fields_and_http_json_refs():
    # Freeze the historical required sets, independently of the current Git HEAD.
    baseline = {
        "ContextSnapshot": "schema_version context_id project_id captured_at item_id version_id view_mode target camera range selected_objects attachments annotations approved_findings",
        "ProjectSnapshot": "schema_version project settings event_sequence active_work held_requests items jobs pending_decisions",
        "ActivityEventData": "invocation_id name state summary artifact_ids model_id",
        "RequestRecord": "schema_version request_id project_id submission receipt accepted_at effective_profile_id generation_task_id",
        "WorkRecord": "schema_version work_id project_id originating_request_id directive_request_ids state state_revision native_session_id job_ids generation_task_id",
        "Artifact": "schema_version artifact_id project_id kind purpose sha256 bytes media_type state derived_from",
    }
    for name, required in baseline.items():
        assert SCHEMA["$defs"][name]["required"] == required.split()
    api = yaml.safe_load((ROOT / "docs/contracts/openapi.yaml").read_text())

    def resolve(value):
        while value.get("$ref", "").startswith("#/components/"):
            _, _, section, name = value["$ref"].split("/")
            value = api["components"][section][name]
        return value

    for methods in api["paths"].values():
        for operation in methods.values():
            if not isinstance(operation, dict) or "operationId" not in operation:
                continue
            envelopes = [operation.get("requestBody", {})] + [
                response
                for status, response in operation["responses"].items()
                if str(status).startswith("2")
            ]
            for envelope in envelopes:
                content = resolve(envelope).get("content", {})
                if "application/json" in content:
                    assert content["application/json"]["schema"]["$ref"].startswith(
                        "./schema.json#/$defs/"
                    )
    operations = (ROOT / "apps/frontend/src/api/generated/operations.ts").read_text()
    for name, definition in (
        ("getResumeOperation", "ResumeOperationStatus"),
        ("listRetainedArtifacts", "RetainedArtifactPage"),
    ):
        assert f"{name}: {{ body: never; response: C.{definition}" in operations
        assert f'{name}: {{ method: "GET"' in operations
        line = next(line for line in operations.splitlines() if f"{name}: {{ method:" in line)
        assert "binary: false" in line
