"""Owner imports exercise real Git/LFS, FFmpeg and SQLite without native authoring."""

import copy
import io
import json
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest
from PIL import Image
from proscenium.api.errors import DomainError
from proscenium.auth import Caller
from proscenium.catalogue import install as catalogue_install
from proscenium.configuration import Settings
from proscenium.previews import install as previews_install
from proscenium.previews.assembly import execute
from proscenium.previews.config import REVISION, Rendering
from proscenium.previews.media import encode, verify_video
from proscenium.previews.router import frames, segments
from proscenium.previews.service import worker_caller
from proscenium.projects.importer import import_project
from proscenium.projects.service import Projects
from proscenium.services import Services
from proscenium.sources.common import git, sha
from proscenium.workflows.assembly import prepared

from tools.operations import recovery

P = "imported-project"
OWNER = Caller("owner", "owner-import")
FPS = dict(numerator=24, denominator=1)
DIMENSIONS = dict(width=16, height=16)
IDENTITY = dict(project_id=P, repository_id="imported-repo")


def write(root, path, data):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data if isinstance(data, bytes) else json.dumps(data).encode())
    return dict(path=path, sha256=sha(target.read_bytes()))


def source(*files, app="proscenium", version="1.0", deps=(), commit=None):
    result = dict(
        entry_path=files[0]["path"],
        files=list(files),
        dependencies=list(deps),
        required_runtime=dict(application=app, version=version, features=[]),
    )
    if commit:
        result["source_commit"] = commit
    return result


def pin(item, version=None):
    return dict(item_id=item, version_id=version or item + "-v1")


def item(name, kind, closure, **extras):
    return dict(**pin(name), name=name, kind=kind, source=closure, **extras)


def png(colour):
    stream = io.BytesIO()
    Image.new("RGB", (16, 16), colour).save(stream, format="PNG")
    return stream.getvalue()


def render_spec(shot, start, end, revision="external-renderer-1"):
    return dict(
        intent_id="intent-" + shot,
        shot_id=shot,
        purpose="draft_motion",
        scene_name="Scene",
        camera=None,
        frames=dict(start=start, end=end),
        fps=FPS,
        dimensions=DIMENSIONS,
        engine="CYCLES",
        device="CPU",
        samples=8,
        colour_management=dict(view_transform="Standard", look="None", exposure=0, gamma=1),
        renderer_revision=revision,
        review_output=dict(media_type="image/png", encoding="view_transformed_srgb", bit_depth=8),
    )


def admitted_tool(services):
    services.gate.resume(P, reconciliation_evidence="Offline setup")
    caller = Caller(
        "tool",
        "test-tool",
        P,
        "work-test",
        services.ownership.epoch,
        operations=("publishOutputs", "admitToolJob", "updateJobState"),
        invocation_id="invocation-test",
    )
    with services.registry.project_store(P).transaction() as tx:
        tx.put(
            "work",
            caller.work_id,
            dict(
                state="running",
                native_session_id="test-session",
                originating_request_id="test-request",
            ),
        )
        tx.put(
            "invocations",
            caller.invocation_id,
            dict(state="admitted", work_id=caller.work_id, owner_epoch=caller.owner_epoch),
        )
    return caller


def watermark(services):
    with services.registry.project_store(P, active=False).transaction(write=False) as tx:
        return (
            tx.watermark(),
            tx.connection.execute(
                "SELECT namespace, count(*), sum(revision) FROM records GROUP BY namespace ORDER BY namespace"
            ).fetchall(),
            tx.connection.execute("SELECT count(*) FROM operations").fetchone(),
        )


def run_cli(root, data, manifest):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.operations.import_project",
            "--project-root",
            str(root),
            "--data-dir",
            str(data),
            "--manifest",
            str(manifest),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return result


def test_rendering_bounds():
    defaults = Rendering()
    assert (defaults.threads, defaults.timeout, defaults.encode_timeout) == (4, 300, 60)
    assert Rendering(threads=12, timeout=1800, encode_timeout=180).threads == 12
    Rendering(threads=32, timeout=3600, encode_timeout=600)
    for key, value in [("threads", 0), ("threads", 33), ("timeout", 3601), ("encode_timeout", 601)]:
        with pytest.raises(ValueError):
            Rendering(**{key: value})


@pytest.fixture(scope="module")
def film(tmp_path_factory):
    root = tmp_path_factory.mktemp("film") / "project"
    root.mkdir()
    git(root, "init", "-b", "main")
    gif = png((64, 128, 192))
    config = dict(
        ffmpeg="ffmpeg",
        ffprobe="ffprobe",
        encode_timeout=60,
        claim_path=str(root / "media/encode.lock"),
    )
    asset = write(root, "assets/portrait.png", gif)
    preview = write(root, "media/reference.png", gif)
    portrait = item(
        "portrait",
        "asset",
        source(asset),
        description="Owner portrait",
        tags=["portrait"],
        provenance=dict(source_url=None, creator="Owner", licence=None),
        artifacts=[
            dict(
                **preview,
                artifact_id="portrait-reference",
                media_type="image/png",
                kind="image",
                purpose="reference",
            )
        ],
    )
    blend = write(root, "scenes/unified.blend", b"synthetic committed Blender source")
    lead = write(root, "scenes/intro.blend", b"synthetic committed second Blender source")
    grouped = {"intro": [], "unified": []}
    entries, shots, panel_media = [], [], []
    cursor = 1
    for idx, (scene, start, end) in enumerate(
        [
            ("intro", 1, 72),
            ("unified", 1, 336),
            ("unified", 337, 768),
            *(("intro", j, j) for j in range(73, 78)),
        ]
    ):
        shot = f"shot-{idx}"
        job_id = f"render-{idx}"
        spec = render_spec(shot, start, end)
        rendered = [
            dict(source_frame=f, **write(root, f"media/{scene}/frames/{f:08d}.png", gif))
            for f in range(start, end + 1)
        ]
        declaration = dict(
            job_id=job_id, external_job_id=f"external/cue/{idx}", spec=spec, frames=rendered
        )
        if idx == 1:
            segment = root / "media/part-one.mp4"
            encode(root / f"media/{scene}", start, len(rendered), FPS, config, segment)
            declaration["segment"] = dict(
                path="media/part-one.mp4", sha256=sha(segment.read_bytes())
            )
        grouped[scene].append(declaration)
        panels = []
        for role in ("start", "end"):
            aid = f"panel-{idx}-{role}"
            panel_media.append(
                dict(
                    **preview,
                    artifact_id=aid,
                    media_type="image/png",
                    kind="image",
                    purpose="keyframe",
                )
            )
            panels.append(
                dict(
                    frame_id=f"frame-{idx}-{role}",
                    role=role,
                    composition="Wide",
                    artifact_id=aid,
                    state="available",
                )
            )
        shots.append(
            dict(
                shot_id=shot,
                intent=f"Shot {idx}",
                camera_intent="Static",
                motion_beats=[],
                scene_version=pin(scene),
                source_range=spec["frames"],
                fps=FPS,
                frames=panels,
            )
        )
        entries.append(
            dict(
                entry_id=f"entry-{idx}",
                shot_id=shot,
                version_id=pin(scene)["version_id"],
                job_id=job_id,
                source_frames=spec["frames"],
                sequence_frames=dict(start=cursor, end=cursor + len(rendered) - 1),
                transform="identity",
                mapping_artifact_id=None,
            )
        )
        cursor += len(rendered)
    plan = dict(
        schema_version="1.0",
        project_id=P,
        plan_id="story",
        brief_artifact_ids=[],
        asset_dependencies=[pin("portrait")],
        unresolved=[],
        shots=shots,
    )
    storyboard_file = write(root, "storyboards/story.json", plan)
    sequence = dict(
        schema_version="1.0",
        project_id=P,
        sequence_version_id="cut-v1",
        fps=FPS,
        frame_count=cursor - 1,
        entries=entries,
        silent=True,
    )
    seq_source = write(root, "sequences/film.json", sequence)
    for f in range(1, cursor):
        write(root, f"media/cut/frames/{f:08d}.png", gif)
    delivery = root / "media/film.mp4"
    encode(root / "media/cut", 1, cursor - 1, FPS, config, delivery)
    unity_scene = write(root, "unity/walk/Assets/Scenes/Main.unity", b"Unity YAML fixture")
    unity = item(
        "walk",
        "unity_result",
        source(unity_scene, app="unity", version="6000.3.27f1", deps=[pin("portrait")]),
        derived_from=[pin("portrait")],
        unity=dict(
            editor_version="6000.3.27f1",
            pipeline="urp",
            scene_path=unity_scene["path"],
            validation=dict(status="not_run"),
        ),
    )
    manifest = dict(
        schema_version="1.0",
        import_id="import-film",
        project=dict(**IDENTITY, name="Example"),
        items=[
            portrait,
            item(
                "unified",
                "scene",
                source(blend, asset, app="blender", version="4.5.16 LTS", deps=[pin("portrait")]),
                renders=grouped["unified"],
            ),
            item(
                "intro",
                "scene",
                source(lead, app="blender", version="4.5.16 LTS"),
                renders=grouped["intro"],
            ),
            item(
                "story",
                "storyboard",
                source(storyboard_file, deps=[pin("portrait"), pin("intro"), pin("unified")]),
                artifacts=panel_media,
            ),
            item(
                "cut",
                "sequence",
                source(seq_source, deps=[pin("intro"), pin("unified")]),
                delivery=dict(
                    job_id="encode-cut",
                    external_job_id="external/film/1",
                    dimensions=DIMENSIONS,
                    video=dict(path="media/film.mp4", sha256=sha(delivery.read_bytes())),
                ),
            ),
            unity,
        ],
        retained_artifacts=[
            dict(
                path="media/film.mp4",
                sha256=sha(delivery.read_bytes()),
                kind="video",
                purpose="final",
                media_type="video/mp4",
                artifact_id="historical-cut",
                external_id="prior-delivery-1",
            )
        ],
        selections=[
            dict(
                selection_id="chosen-panel",
                choice=dict(kind="artifact", artifact_id="panel-0-start"),
                slot=dict(
                    kind="storyboard_frame",
                    storyboard_version=pin("story"),
                    shot_id="shot-0",
                    frame_id="frame-0-start",
                ),
            )
        ],
    )
    manifest_file = root.parent / "manifest.json"
    manifest_file.write_text(json.dumps(manifest))
    (root / ".gitignore").write_text("media/\ncustom-cache/\n")
    (root / ".gitattributes").write_text("*.custom text\n")
    return root, manifest, manifest_file, config


def test_import_split_replay_reassembly_rebind_and_recovery(film):
    root, manifest, manifest_file, config = film
    settings = Settings(
        data_directory=root.parent / "registry",
        free_space_reserve_bytes=0,
        execution_profiles=frozenset({"blender-cpu"}),
    )
    initial = json.loads(run_cli(root, settings.data_directory, manifest_file).stdout)
    assert not initial["replayed"] and len(initial["job_ids"]) == 9
    assert "custom-cache/" in (root / ".gitignore").read_text()
    assert (root / ".gitattributes").read_text().endswith("*.custom text\n")
    assert b"git-lfs" in git(root, "show", "HEAD:scenes/unified.blend")
    services = Services.create(settings)
    snapshot = watermark(services)
    services.close()
    assert json.loads(run_cli(root, settings.data_directory, manifest_file).stdout) == {
        **initial,
        "replayed": True,
    }

    # Recovery and rebind run before admitting any new native/tool work.
    services = Services.create(settings)
    assert watermark(services) == snapshot
    with pytest.raises(DomainError, match="idempotency conflict"):
        import_project(services, OWNER, root, {**manifest, "selections": []})
    moved = root.with_name("moved")
    root.rename(moved)
    Projects(services).rebind(
        OWNER,
        dict(
            schema_version="1.0",
            operation_id="rebind-film",
            project_id=P,
            expected_revision=0,
            new_path=str(moved),
        ),
    )
    root = moved
    config["claim_path"] = str(root / "media/encode.lock")
    assert import_project(services, OWNER, root, manifest)["replayed"]
    services.close()
    backup = root.parent / "backup"
    recovery.backup(settings, P, backup)
    assert (
        recovery.verify(backup)["source"]["head"] == git(root, "rev-parse", "HEAD").decode().strip()
    )

    services = Services.create(settings)
    try:
        catalogue = catalogue_install(services)
        previews = previews_install(services)
        services.activate_project(P)
        assert previews.reconcile(P) == []
        scene = catalogue.version(P, "unified-v1")
        assert scene["preview_job_ids"] == ["render-1", "render-2"]
        assert (
            services.extension("sources").materialise(P, "unified-v1") / "assets/portrait.png"
        ).is_file()
        assert catalogue.version(P, "walk-v1")["derived_from"] == [pin("portrait")]
        shot_plan = catalogue.shot_plan(P, "story-v1")
        assert len(shot_plan["shots"]) == 8 and shot_plan["shots"][0]["frames"][0]["owner_selected"]
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(services=services)))
        for job, first, last in [("render-1", 1, 336), ("render-2", 337, 768)]:
            ready = previews.availability(P, job)
            assert (
                ready["playable_ranges"]
                == ready["available_ranges"]
                == [dict(start=first, end=last)]
            )
            frame_page = frames(request, P, job, OWNER, from_frame=last, to_frame=last)
            assert [r["source_frame"] for r in frame_page["items"]] == [last]
            assert (
                segments(request, P, job, OWNER)["items"][0]["source_frames"]
                == ready["available_ranges"][0]
            )
        cut = catalogue.version(P, "cut-v1")
        artifacts = services.extension("artifacts")
        mapping = next(a for a in cut["artifact_ids"] if artifacts.get(P, a)["kind"] == "frame_map")
        full = json.loads(artifacts.ready_path(P, mapping).read_bytes())
        assert full["frames"][408] == dict(
            sequence_frame=409,
            contributors=[
                dict(
                    job_id="render-2",
                    shot_id="shot-2",
                    version_id="unified-v1",
                    source_frame=337,
                    weight=dict(numerator=1, denominator=1),
                )
            ],
        )
        with services.registry.project_store(P).transaction(write=False) as tx:
            assert (
                catalogue.item_view(tx, tx.require("items", "cut"))["selected_version_id"] is None
            )
            assert (
                tx.require("attempts", tx.require("jobs", "encode-cut")["attempt_id"])["state"]
                == "succeeded"
            )
            assert (
                tx.require("imported_artifact_evidence", "historical-cut")["external_id"]
                == "prior-delivery-1"
            )

        revise_and_reassemble(services, root, config, scene, cut)
    finally:
        services.close()


def publish_row(version, new_id, closure=None, commit=None, requests=()):
    excluded = {
        "schema_version",
        "project_id",
        "publication_id",
        "created_at",
        "actor",
        "preview_job_ids",
    }
    return {
        **{k: v for k, v in version.items() if k not in excluded},
        "version_id": new_id,
        "expected_current_version_id": version["version_id"],
        "source": closure or version["source"],
        "source_commit": commit or version["source_commit"],
        "artifact_ids": [],
        "preview_requests": list(requests),
    }


def revised_source(services, root, old, updated, label):
    git(root, "add", "--", updated["path"])
    git(
        root,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        label,
    )
    commit = git(root, "rev-parse", "HEAD").decode().strip()
    closure = copy.deepcopy(old["source"])
    for entry in closure["files"]:
        if entry["destination"] == updated["path"]:
            entry["origin"].update(commit=commit, sha256=updated["sha256"])
    from proscenium.sources import closure_digest

    closure["digest"] = closure_digest(closure)
    return closure, commit


def revise_and_reassemble(services, root, config, scene, cut):
    catalogue = services.extension("catalogue")
    previews = services.extension("previews")
    caller = admitted_tool(services)
    update = write(root, "scenes/unified.blend", b"modified synthetic Blender source")
    closure, commit = revised_source(services, root, scene, update, "Revise scene")
    spec = render_spec("shot-2", 337, 338, REVISION)
    spec.update(source_closure_digest=closure["digest"], intent_id="new-render-intent")
    publication, _ = catalogue.publish(
        caller,
        dict(
            schema_version="1.0",
            project_id=P,
            publication_id="revise-scene",
            items=[publish_row(scene, "unified-v2", closure, commit, [spec])],
        ),
    )
    job = services.jobs.get(P, publication["job_ids"][0])
    job_dir = previews.root(job)
    write(job_dir, "task.json", {key: job[key] for key in ("job_id", "attempt_id", "input_digest")})
    worker = worker_caller(job)
    new_frame = png((230, 15, 20))
    for frame in (337, 338):
        file = write(job_dir, f"frames/{frame:08d}.png", new_frame)
        previews.register(
            worker,
            dict(
                schema_version="1.0",
                project_id=P,
                job_id=job["job_id"],
                attempt_id=job["attempt_id"],
                input_digest=job["input_digest"],
                version_id="unified-v2",
                shot_id="shot-2",
                source_frame=frame,
                dimensions=DIMENSIONS,
                output_path=file["path"],
                sha256=file["sha256"],
                receipt_id=f"new-receipt-{frame}",
                artifact_id=f"new-frame-{frame}",
            ),
        )
    previews.state(job, "running", evidence="Hermetic worker fixture")
    assert (
        previews.state(job, "succeeded", evidence="Hermetic worker fixture", exit_code=0)[
            "availability"
        ]
        == "complete"
    )

    # Keep every unchanged shot and replace only the second half of the split cue.
    sequence = catalogue.sequence_map(P, "cut-v1")
    sequence.update(sequence_version_id="cut-v2", frame_count=415)
    original = copy.deepcopy(sequence["entries"])
    sequence["entries"][2].update(
        transform="trim",
        source_frames=dict(start=337, end=338),
        sequence_frames=dict(start=409, end=410),
        job_id=job["job_id"],
        version_id="unified-v2",
    )
    for entry in sequence["entries"][3:]:
        entry["sequence_frames"] = {
            key: value - 430 for key, value in entry["sequence_frames"].items()
        }
    update = write(root, "sequences/film.json", sequence)
    closure, commit = revised_source(services, root, cut, update, "Revise sequence")
    closure["dependencies"] = [pin("intro"), pin("unified"), pin("unified", "unified-v2")]
    from proscenium.sources import closure_digest

    closure["digest"] = closure_digest(closure)
    catalogue.publish(
        caller,
        dict(
            schema_version="1.0",
            project_id=P,
            publication_id="revise-cut",
            items=[publish_row(cut, "cut-v2", closure, commit)],
        ),
    )
    admission, seq, frame_map = prepared(
        services,
        caller,
        dict(
            operation_id="assemble-v2",
            job_id="assembly-v2",
            version=pin("cut", "cut-v2"),
            execution_profile_id="blender-cpu",
        ),
    )
    encode_job, _ = previews.assembly(caller, admission, seq, frame_map)
    with services.registry.project_store(P).transaction(write=False) as tx:
        snap = tx.require("preview_assemblies", encode_job["job_id"])
    assert {(r["job_id"], r["source_frame"]) for r in snap["input_frames"]} == {
        *(
            (entry["job_id"], frame)
            for entry in original
            if entry["job_id"] != "render-2"
            for frame in range(entry["source_frames"]["start"], entry["source_frames"]["end"] + 1)
        ),
        (job["job_id"], 337),
        (job["job_id"], 338),
    }
    output = root / "media/reassembly"
    inputs = output / "assembly-inputs"
    inputs.mkdir(parents=True)
    existing = set()
    for receipt in snap["input_frames"]:
        if receipt["sha256"] not in existing:
            path = services.extension("artifacts").ready_path(P, receipt["artifact_id"])
            shutil.copyfile(path, inputs / (receipt["sha256"] + ".png"))
            existing.add(receipt["sha256"])
    execute(output, dict(assembly=snap, input_digest=encode_job["input_digest"], config=config))
    assert json.loads((output / "frame-map.json").read_bytes()) == frame_map
    assert verify_video(output / "delivery.mp4", FPS, 415, DIMENSIONS, config)["pts"][408] == "17"
    assert catalogue.sequence_map(P, "cut-v1")["frame_count"] == 845


def test_explicit_commit_and_bad_digest_are_not_worktree_fallbacks(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    git(root, "init", "-b", "main")
    old = write(root, "asset.txt", b"pinned")
    git(root, "add", "asset.txt")
    git(
        root,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "base",
    )
    commit = git(root, "rev-parse", "HEAD").decode().strip()
    index = (root / ".git/index").read_bytes()
    dirty = write(root, "asset.txt", b"different")
    services = Services.create(Settings(data_directory=tmp_path / "registry"))
    try:
        bad = dict(
            schema_version="1.0",
            import_id="explicit",
            project=IDENTITY,
            items=[item("doc", "asset", source(dirty, commit=commit))],
        )
        with pytest.raises(DomainError, match="hash mismatch"):
            import_project(services, OWNER, root, bad)
        with services.registry.project_store(P, active=False).transaction(write=False) as tx:
            assert not tx.all("versions") and tx.watermark() == 0
        good = {**bad, "items": [item("doc", "asset", source(old, commit=commit))]}
        assert import_project(services, OWNER, root, good)["versions"] == [pin("doc")]
        assert git(root, "rev-parse", "HEAD").decode().strip() == commit
        assert (root / ".git/index").read_bytes() == index
    finally:
        services.close()


@pytest.mark.parametrize("corruption", ["wrong-hash", "wrong-mode", "missing-frame"])
def test_failed_render_verification_does_not_publish(tmp_path, corruption):
    root = tmp_path / "project"
    root.mkdir()
    git(root, "init", "-b", "main")
    blend = write(root, "scenes/example.blend", b"pinned")
    stream = io.BytesIO()
    Image.new("RGBA" if corruption == "wrong-mode" else "RGB", (16, 16)).save(stream, format="PNG")
    data = write(root, "media/frame.png", stream.getvalue())
    if corruption == "wrong-hash":
        data["sha256"] = "0" * 64
    render = dict(
        job_id="bad-render",
        external_job_id="external/fixture",
        spec=render_spec("shot", 1, 1),
        frames=[] if corruption == "missing-frame" else [dict(source_frame=1, **data)],
    )
    manifest = dict(
        schema_version="1.0",
        import_id="bad-render-import",
        project=IDENTITY,
        items=[item("scene", "scene", source(blend, app="blender"), renders=[render])],
    )
    services = Services.create(Settings(data_directory=tmp_path / "registry"))
    try:
        with pytest.raises(DomainError) as error:
            import_project(services, OWNER, root, manifest)
        assert (
            error.value.code
            == {
                "wrong-hash": "hash_mismatch",
                "wrong-mode": "media_unavailable",
                "missing-frame": "invalid_frame_mapping",
            }[corruption]
        )
        with services.registry.project_store(P, active=False).transaction(write=False) as tx:
            for namespace in (
                "versions",
                "items",
                "jobs",
                "attempts",
                "artifacts",
                "frames",
                "segments",
            ):
                assert tx.all(namespace) == []
            assert tx.watermark() == 0
    finally:
        services.close()
