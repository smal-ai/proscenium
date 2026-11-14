import asyncio
import hashlib
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image
from proscenium.api.errors import DomainError
from test_delivery import PROJECT, context, submit
from test_delivery import runtime as runtime


class Artifacts:
    def __init__(self):
        self.records, self.paths = {}, {}

    def get(self, project_id, artifact_id):
        return self.records[artifact_id]

    def ready_path(self, project_id, artifact_id):
        return self.paths[artifact_id]


def image(runtime, identity, *, mode="RGB", colour="red", size=(16, 16)):
    service = runtime.services.extensions.setdefault("artifacts", Artifacts())
    root = Path(runtime.services.registry.get(PROJECT)["path"])
    path = root / ".proscenium/artefacts" / (identity + ".png")
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new(mode, size, colour).save(path)
    record = dict(
        schema_version="1.0",
        artifact_id=identity,
        project_id=PROJECT,
        kind="mask" if mode == "L" else "image",
        purpose="mask" if mode == "L" else "reference",
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        bytes=path.stat().st_size,
        media_type="image/png",
        state="ready",
        derived_from=[],
        dimensions={"width": size[0], "height": size[1]},
    )
    service.records[identity], service.paths[identity] = record, path
    target = dict(
        origin="reference",
        artifact_id=identity,
        displayed_sha256=record["sha256"],
        dimensions=record["dimensions"],
        item_id=None,
        version_id=None,
        shot_id=None,
        render_job_id=None,
        source_frame=None,
        media_artifact_id=None,
    )
    return target, path


def annotation(target, *, identity="annotation-one", **fields):
    return dict(
        annotation_id=identity,
        saved_revision=None,
        target=target,
        range=None,
        camera=None,
        markups=[],
        text="Owner feedback",
        spatial_hits=[],
        spatial_status="not_requested",
        **fields,
    )


def test_images_persist_and_annotations_keep_independent_targets(runtime):
    first, _ = image(runtime, "image-one")
    second, _ = image(runtime, "image-two", colour="blue")
    snapshot = context(runtime, target=first, annotations=[annotation(second)])
    assert runtime.contexts.get(PROJECT, snapshot["context_id"]) == snapshot
    submit(runtime)

    async def run():
        await runtime.send(PROJECT, "request-one")
        attached = runtime.native.session.sent[0][1]["attachments"]
        assert [Path(a["path"]).name for a in attached[:-1]] == ["image-one.png", "image-two.png"]
        assert attached[-1]["payload"]["annotations"][0]["target"] == second
        await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "mutation,code",
    [("hash", "hash_mismatch"), ("state", "media_unavailable"), ("escape", "scope_denied")],
)
def test_changed_unready_or_outside_artifact_rejected(runtime, tmp_path, mutation, code):
    target, path = image(runtime, "image-one")
    if mutation == "hash":
        path.write_bytes(b"not the admitted image")
    elif mutation == "state":
        runtime.services.extensions["artifacts"].records["image-one"]["state"] = "unavailable"
    else:
        outside = tmp_path / "outside.png"
        outside.write_bytes(path.read_bytes())
        runtime.services.extensions["artifacts"].paths["image-one"] = outside
    with pytest.raises(DomainError) as caught:
        context(runtime, target=target)
    assert caught.value.code == code


@pytest.mark.parametrize(
    "limit",
    ["attachment_max_count", "attachment_max_bytes", "upload_max_pixels", "upload_max_bytes"],
)
def test_aggregate_count_bytes_and_decode_limits(runtime, limit):
    target, _ = image(runtime, "image-one")
    other, _ = image(runtime, "image-two")
    runtime.services.settings = replace(runtime.services.settings, **{limit: 1})
    with pytest.raises(DomainError) as caught:
        context(runtime, target=target, annotations=[annotation(other)])
    assert caught.value.code in {"attachment_limit_exceeded", "image_dimensions_exceeded"}


@pytest.mark.parametrize("bad", ["dimensions", "encoding", "binary_values"])
def test_mask_validity_is_checked_against_its_annotation_target(runtime, bad):
    target, _ = image(runtime, "target-image")
    mask, path = image(
        runtime, "mask-image", mode="L", colour=0, size=(8, 8) if bad == "dimensions" else (16, 16)
    )
    if bad == "encoding":
        Image.new("RGB", (16, 16), "black").save(path)
    if bad == "binary_values":
        Image.new("L", (16, 16), 127).save(path)
    registered = runtime.services.extensions["artifacts"].records["mask-image"]
    registered.update(
        bytes=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest()
    )
    markup = dict(
        id="mask-markup",
        kind="mask",
        points=[[0.2, 0.3]],
        mask_artifact_id="mask-image",
        mask_encoding=dict(
            channel="luma", bit_depth=8, selected_value=255, unselected_value=0, semantics="binary"
        ),
    )
    item = annotation(target)
    item["markups"] = [markup]
    with pytest.raises(DomainError) as caught:
        context(runtime, annotations=[item])
    assert caught.value.code == "frame_conflict"


def test_owner_selected_findings_and_atomic_link_fail_closed(runtime):
    target, _ = image(runtime, "finding-image")
    context(
        runtime,
        attachments=[{"artifact_id": target["artifact_id"], "role": "reference"}],
        approved_findings=[
            dict(
                finding_id="finding-one",
                finding_revision=1,
                approved_text="Only this exact owner-selected finding",
                target=target,
            )
        ],
    )
    with pytest.raises(DomainError) as caught:
        submit(runtime)
    assert caught.value.code == "capability_unavailable"

    class Reviews:
        def validate_and_submit_findings(self, tx, request_id, findings):
            tx.put("copilot.test_findings", request_id, {"findings": findings})
            raise DomainError("stale_decision", 409)

    runtime.services.extensions["reviews"] = Reviews()
    with pytest.raises(DomainError):
        submit(runtime)
    with runtime.services.registry.project_store(PROJECT).transaction(write=False) as tx:
        assert tx.all("requests") == []
        assert tx.all("copilot.test_findings") == []
    assert runtime.native.opens == []


def test_finding_submission_requires_clean_target_in_original_attachments(runtime):
    target, _ = image(runtime, "finding-without-clean-attachment")
    context(
        runtime,
        approved_findings=[
            dict(
                finding_id="finding-one",
                finding_revision=1,
                approved_text="Selected finding",
                target=target,
            )
        ],
    )
    with pytest.raises(DomainError) as caught:
        submit(runtime)
    assert caught.value.code == "invalid_request"
    with runtime.services.registry.project_store(PROJECT).transaction(write=False) as tx:
        assert not tx.all("requests")
