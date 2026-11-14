"""Real W02 promotion/publication and F00 admission, with no external inference."""

import io
import json
import runpy
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest
from PIL import Image
from proscenium.api.errors import DomainError
from proscenium.catalogue import install as install_catalogue
from proscenium.contracts import canonical
from proscenium.projects.service import Projects
from proscenium.providers import install, register_provider_artifact
from proscenium.providers.klein import graph
from proscenium.sources import closure_digest
from proscenium.sources.common import git, sha


def publish(
    services,
    caller,
    root,
    version_id,
    *,
    item_id="plan-item",
    head=None,
    content=None,
    kind="storyboard",
    dependencies=None,
):
    if content is None:
        content = json.loads(
            (
                Path(__file__).parents[3] / "docs/contracts/examples/authored-shot-plan.json"
            ).read_bytes()
        )
        content["project_id"] = "project-test"
        for shot in content["shots"]:
            for frame in shot["frames"]:
                frame["artifact_id"] = None
                frame["state"] = "missing"
    raw = canonical(content)
    name = f"storyboards/{version_id}.json"
    (root / "storyboards").mkdir(exist_ok=True)
    (root / name).write_bytes(raw)
    git(root, "add", "--", name, ".gitattributes")
    git(root, "commit", "-m", "Disposable provider identity fixture")
    revision = git(root, "rev-parse", "HEAD").decode().strip()
    source = dict(
        entry_path=name,
        files=[
            dict(
                destination=name,
                origin=dict(
                    kind="git",
                    repository_id="repo-test",
                    commit=revision,
                    path=name,
                    sha256=sha(raw),
                ),
            )
        ],
        dependencies=dependencies or [],
        required_runtime=dict(application="fixture", version="1", features=[]),
        digest="0" * 64,
    )
    source["digest"] = closure_digest(source)
    body = dict(
        schema_version="1.0",
        project_id="project-test",
        publication_id="publication-" + version_id,
        items=[
            dict(
                item_id=item_id,
                version_id=version_id,
                kind=kind,
                name="Provider fixture",
                expected_current_version_id=head,
                source_commit=revision,
                source=source,
                artifact_ids=[],
                preview_requests=[],
            )
        ],
    )
    services.extension("catalogue").publish(caller, body)
    return content


@pytest.fixture
def composed(services, caller):
    root = Path(services.registry.get("project-test")["path"])
    Projects.bootstrap(root, "Disposable provider integration")
    git(root, "config", "user.name", "Provider tests")
    git(root, "config", "user.email", "providers@example.invalid")
    services.settings = replace(
        services.settings,
        services=(
            dict(
                provider_id="comfy-test",
                endpoint="http://offline.invalid:8188",
                capabilities=["image"],
                model_id=None,
                available=None,
            ),
        ),
    )
    install_catalogue(services)
    services.gate.resume("project-test", reconciliation_evidence="explicit offline test allocation")
    caller = replace(
        caller,
        invocation_id="provider-invocation",
        operations=(
            "submitGenerationJob",
            "registerToolArtifact",
            "publishOutputs",
            "updateJobState",
        ),
    )
    with services.registry.project_store("project-test").transaction() as tx:
        tx.put(
            "invocations",
            caller.invocation_id,
            dict(work_id=caller.work_id, owner_epoch=caller.owner_epoch, state="running"),
        )
        tx.put(
            "project_identity",
            "identity",
            dict(project_id="project-test", repository_id="repo-test"),
        )
        tx.put("copilot.effective", "request-test", dict(generation_attempt_limit=2))
    artifacts = services.extension("artifacts")
    workflow = graph(
        unet="klein.safetensors",
        clip="qwen.safetensors",
        vae="flux2.safetensors",
        width=64,
        height=64,
    )
    for identity, data in [
        ("workflow-test", canonical(workflow)),
        ("prompt-test", b"One red square"),
    ]:
        artifact = artifacts.promote(
            "project-test",
            identity,
            data,
            kind="document",
            purpose="metadata",
            media_type="application/json",
        )
        with services.registry.project_store("project-test").transaction() as tx:
            artifacts.put(tx, artifact)
    transport = httpx.Client(
        transport=httpx.MockTransport(lambda r: pytest.fail("No HTTP expected"))
    )
    adapter = install(
        services,
        client=transport,
        registrar=lambda c, r: register_provider_artifact(services, c, r),
    )
    yield services, caller, root, artifacts, adapter
    transport.close()


def slot(version, frame="frame-start", item="plan-item"):
    return dict(
        kind="storyboard_frame",
        storyboard_version=dict(item_id=item, version_id=version),
        shot_id="shot-fixture",
        frame_id=frame,
    )


def test_alias_and_plan_revision_share_canonical_budget(composed, generation):
    services, caller, root, _, adapter = composed
    publish(services, caller, root, "plan-v1")
    first, _ = adapter.submit(
        caller,
        dict(generation, settings={"target_slot": slot("plan-v1")}, target_id="caller-alias-one"),
    )
    publish(services, caller, root, "plan-v2", head="plan-v1")
    second, _ = adapter.submit(
        caller,
        dict(
            generation,
            job_id="job-two",
            settings={"target_slot": slot("plan-v2")},
            target_id="renamed-alias",
        ),
    )
    assert first["target"]["target_id"] == second["target"]["target_id"]
    assert first["target"]["version"]["version_id"] == "plan-v1"
    assert second["target"]["version"]["version_id"] == "plan-v2"
    assert second["target"]["shot_id"] == "shot-fixture"
    services.settings = replace(services.settings, generation_attempt_limit=999)
    with pytest.raises(DomainError) as error:
        adapter.submit(
            caller,
            dict(
                generation,
                job_id="job-three",
                settings={"target_slot": slot("plan-v2")},
                target_id="third-alias",
            ),
        )
    assert error.value.code == "generation_budget_exhausted"
    third, _ = adapter.submit(
        caller,
        dict(
            generation,
            job_id="different-frame",
            settings={"target_slot": slot("plan-v2", "frame-end")},
        ),
    )
    assert third["target"]["target_id"] != first["target"]["target_id"]
    assert (
        services.jobs.inputs("project-test", first["job_id"])["request"]["target_id"]
        == "caller-alias-one"
    )


@pytest.mark.parametrize(
    "mutation", ["missing-frame", "wrong-item", "missing-version", "other-project"]
)
def test_nonexistent_or_outside_slot_cannot_bind_policy(composed, generation, mutation):
    services, caller, root, _, adapter = composed
    content = None
    if mutation == "other-project":
        # A foreign version cannot be manufactured through actual publication.
        generation = dict(generation, project_id="outside-project")
    else:
        content = publish(services, caller, root, "plan-v1")
    target = slot("plan-v1")
    if mutation == "missing-frame":
        target["frame_id"] = "nonexistent"
    if mutation == "wrong-item":
        target["storyboard_version"]["item_id"] = "missing-item"
    if mutation == "missing-version":
        target["storyboard_version"]["version_id"] = "missing-version"
    with pytest.raises(DomainError):
        adapter.submit(caller, dict(generation, settings={"target_slot": target}))
    with services.registry.project_store("project-test").transaction(write=False) as tx:
        assert tx.get("providers.tasks", "task-test", required=False) is None
        assert not tx.all("jobs")
    assert content is None or content["project_id"] == "project-test"


def test_standalone_task_target_and_item_slot(composed, generation):
    services, caller, root, _, adapter = composed
    services.settings = replace(services.settings, generation_attempt_limit=999)
    with pytest.raises(DomainError):
        adapter.submit(caller, generation)
    standalone, _ = adapter.submit(caller, dict(generation, target_id="task-test"))
    assert standalone["target"] == dict(target_id="task-test", version=None, shot_id=None)
    publish(services, caller, root, "item-v1")
    item, _ = adapter.submit(
        caller,
        dict(
            generation,
            job_id="item-job",
            settings={"target_slot": dict(kind="item", item_id="plan-item")},
        ),
    )
    assert item["target"] == dict(
        target_id="plan-item", version=dict(item_id="plan-item", version_id="item-v1"), shot_id=None
    )
    assert (
        services.jobs.inputs("project-test", item["job_id"])["target_resolution"]["item"]["item_id"]
        == "plan-item"
    )
    with services.registry.project_store("project-test").transaction(write=False) as tx:
        assert tx.require("providers.tasks", "task-test")["limit"] == 2


def stage(services, adapter, caller, root, generation):
    job, _ = adapter.submit(caller, dict(generation, target_id="task-test"))
    stream = io.BytesIO()
    Image.new("RGB", (64, 64), "red").save(stream, format="PNG")
    data = stream.getvalue()
    path = root / ".proscenium/jobs" / job["job_id"] / "output.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    request = dict(
        schema_version="1.0",
        operation_id="register-output",
        project_id="project-test",
        artifact_id="output-test",
        staging_path=path.relative_to(root).as_posix(),
        kind="image",
        purpose="concept",
        media_type="image/png",
        sha256=sha(data),
    )
    return job, path, request


def test_real_bridge_promotes_replays_after_terminal_and_missing_staging(composed, generation):
    services, caller, root, artifacts, adapter = composed
    job, path, request = stage(services, adapter, caller, root, generation)
    worker = adapter.worker(job)
    artifact = register_provider_artifact(services, worker, request)
    assert artifact["dimensions"] == {"width": 64, "height": 64}
    assert (
        artifacts.ready_path("project-test", artifact["artifact_id"]).read_bytes()
        == path.read_bytes()
    )
    adapter.update(job, "failed")
    path.unlink()
    assert register_provider_artifact(services, worker, request) == artifact
    with pytest.raises(DomainError) as error:
        register_provider_artifact(services, worker, dict(request, purpose="reference"))
    assert error.value.code == "idempotency_conflict"


@pytest.mark.parametrize(
    "bad",
    ["tool", "attempt", "digest", "job", "allowlist", "other-path", "symlink", "secret", "hash"],
)
def test_bridge_rejects_unbound_or_unsafe_output(composed, generation, bad):
    services, caller, root, _, adapter = composed
    job, path, request = stage(services, adapter, caller, root, generation)
    worker = adapter.worker(job)
    if bad == "tool":
        worker = caller
    if bad == "attempt":
        worker = replace(worker, attempt_id="foreign-attempt")
    if bad == "digest":
        worker = replace(worker, input_digest="0" * 64)
    if bad == "job":
        worker = replace(worker, job_id="foreign-job")
    if bad == "allowlist":
        worker = replace(worker, operations=())
    if bad == "other-path":
        request["staging_path"] = ".proscenium/jobs/foreign-job/output.png"
    if bad in {"symlink", "secret"}:
        other = path.with_name("linked.png" if bad == "symlink" else ".env")
        if bad == "symlink":
            other.symlink_to(path)
        else:
            other.write_bytes(path.read_bytes())
        request["staging_path"] = other.relative_to(root).as_posix()
    if bad == "hash":
        request["sha256"] = "0" * 64
    with pytest.raises(DomainError):
        register_provider_artifact(services, worker, request)
    with services.registry.project_store("project-test").transaction(write=False) as tx:
        assert tx.get("artifacts", request["artifact_id"], required=False) is None


def test_item_pins_originating_captured_version_not_new_head(composed, generation):
    services, caller, root, _, adapter = composed
    publish(services, caller, root, "captured-version")
    publish(services, caller, root, "new-head", head="captured-version")
    context: dict[str, Any] = dict(
        schema_version="1.0",
        context_id="captured-context",
        project_id="project-test",
        captured_at="2026-11-14T14:00:00+02:00",
        item_id="plan-item",
        version_id="captured-version",
        view_mode="none",
        target=None,
        camera=None,
        range=None,
        selected_objects=[],
        attachments=[],
        annotations=[],
        approved_findings=[],
    )
    with services.registry.project_store("project-test").transaction() as tx:
        tx.put("contexts", context["context_id"], context, schema="ContextSnapshot")
        # This test concerns identity lookup, not a second implementation of Queue admission.
        tx.put("requests", "request-test", {"submission": {"context_id": context["context_id"]}})
    job, _ = adapter.submit(caller, dict(generation, target_id="plan-item"))
    assert job["target"]["version"] == dict(item_id="plan-item", version_id="captured-version")
    assert (
        services.jobs.inputs("project-test", job["job_id"])["target_resolution"]["context"]
        == context
    )


def test_bridge_preserves_source_version_and_transformations(composed, generation):
    services, caller, root, artifacts, adapter = composed
    publish(services, caller, root, "source-version")
    job, _ = adapter.submit(caller, dict(generation, target_id="plan-item"))
    output = io.BytesIO()
    Image.new("RGB", (64, 64), "blue").save(output, format="PNG")
    original = artifacts.promote(
        "project-test",
        "derived-input",
        output.getvalue(),
        kind="image",
        purpose="reference",
        media_type="image/png",
    )
    with services.registry.project_store("project-test").transaction() as tx:
        artifacts.put(tx, original)
    relative = f".proscenium/jobs/{job['job_id']}/derived.png"
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(output.getvalue())
    transformation = dict(
        kind="encode", input_artifact_ids=["derived-input"], parameters={"encoding": "RGB PNG"}
    )
    request = dict(
        schema_version="1.0",
        operation_id="derived-register",
        project_id="project-test",
        artifact_id="derived-output",
        staging_path=relative,
        kind="image",
        purpose="concept",
        media_type="image/png",
        sha256=sha(output.getvalue()),
        derived_from=[transformation],
    )
    registered = register_provider_artifact(services, adapter.worker(job), request)
    assert registered["source_version_id"] == "source-version"
    assert registered["dimensions"] == {"width": 64, "height": 64}
    assert registered["derived_from"] == [transformation]
    request["operation_id"] = "invalid-derived-register"
    request["artifact_id"] = "invalid-derived-output"
    request["derived_from"] = [dict(transformation, input_artifact_ids=["missing-input"])]
    with pytest.raises(DomainError):
        register_provider_artifact(services, adapter.worker(job), request)


def test_sequence_slot_budget_stable_across_versions_and_entry_specific(composed, generation):
    services, caller, root, _, adapter = composed
    publish(
        services,
        caller,
        root,
        "scene-version",
        kind="scene",
        item_id="scene-item",
        content={"fixture": True},
    )
    with services.registry.project_store("project-test").transaction() as tx:
        scene = tx.require("versions", "scene-version")
        spec = json.loads(
            (
                Path(__file__).parents[3] / "docs/contracts/examples/preview-authored-camera.json"
            ).read_bytes()
        )["spec"]
        spec["source_closure_digest"] = scene["source"]["digest"]
        spec["frames"] = {"start": 1, "end": 4}
        render = services.jobs.create(
            tx,
            job_id="sequence-source-job",
            kind="render",
            target=dict(
                target_id="scene-item",
                version=dict(item_id="scene-item", version_id="scene-version"),
                shot_id=spec["shot_id"],
            ),
            input_digest="a" * 64,
            operation_id="sequence-source",
            work_id=caller.work_id,
            render_spec=spec,
            expected_frames=4,
        )
    entry = dict(
        entry_id="entry-one",
        shot_id=spec["shot_id"],
        version_id="scene-version",
        job_id=render["job_id"],
        source_frames={"start": 1, "end": 2},
        sequence_frames={"start": 1, "end": 2},
        transform="trim",
        mapping_artifact_id=None,
    )
    document = dict(
        schema_version="1.0",
        sequence_version_id="sequence-v1",
        project_id="project-test",
        fps=spec["fps"],
        frame_count=4,
        silent=True,
        entries=[
            entry,
            dict(
                entry,
                entry_id="entry-two",
                source_frames={"start": 3, "end": 4},
                sequence_frames={"start": 3, "end": 4},
            ),
        ],
    )
    head = None
    first = None
    for index in range(1, 3):
        version = f"sequence-v{index}"
        document["sequence_version_id"] = version
        publish(
            services,
            caller,
            root,
            version,
            item_id="sequence-item",
            head=head,
            content=document,
            kind="sequence",
            dependencies=[dict(item_id="scene-item", version_id="scene-version")],
        )
        target_slot = dict(
            kind="assembly_shot",
            sequence_version=dict(item_id="sequence-item", version_id=version),
            entry_id="entry-one",
        )
        job, _ = adapter.submit(
            caller,
            dict(
                generation,
                job_id=f"sequence-job-{index}",
                target_id=f"alias-{index}",
                settings={"target_slot": target_slot},
            ),
        )
        if first:
            assert job["target"]["target_id"] == first["target"]["target_id"]
        else:
            first = job
        assert job["target"]["version"]["version_id"] == version
        head = version
    other, _ = adapter.submit(
        caller,
        dict(
            generation,
            job_id="other-sequence-entry",
            settings={"target_slot": dict(target_slot, entry_id="entry-two")},
        ),
    )
    assert first is not None
    assert other["target"]["target_id"] != first["target"]["target_id"]
    with pytest.raises(DomainError) as error:
        adapter.submit(
            caller,
            dict(generation, job_id="sequence-over-budget", settings={"target_slot": target_slot}),
        )
    assert error.value.code == "generation_budget_exhausted"


def test_expired_proof_rerun_preserves_report_without_opening_services(tmp_path, monkeypatch):
    # Synthetic timing-only guard test, not live inference evidence.
    module = runpy.run_path(
        str(Path(__file__).parents[3] / "tools/comfy/prove_generation.py"),
        run_name="allocation_guard_test",
    )
    runner = module["run"]
    runner.__globals__["SCRATCH"] = tmp_path
    report = tmp_path / "w07-live-initial-report.json"
    report.write_text(json.dumps({"job": {"state": "planned"}}))
    before = report.read_bytes()
    (tmp_path / "w07-live-initial-timing.json").write_text(json.dumps({"started_epoch": 0}))
    monkeypatch.setattr(
        module["Services"],
        "create",
        lambda _: pytest.fail("Expired allocation must not open services"),
    )
    with pytest.raises(SystemExit, match="Evidence preserved"):
        runner(correction=False)
    assert report.read_bytes() == before
