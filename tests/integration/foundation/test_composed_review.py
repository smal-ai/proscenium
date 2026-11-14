"""Actual source/artefact/review services share one project transaction safely."""

import io

import pytest
from PIL import Image, PngImagePlugin
from proscenium.api.errors import DomainError
from proscenium.auth import Caller
from proscenium.catalogue import install as install_catalogue
from proscenium.reviews import install as install_reviews


def image_bytes(mode, colour, metadata=None):
    image = Image.new(mode, (8, 8), colour)
    output = io.BytesIO()
    image.save(output, "PNG", pnginfo=metadata)
    return output.getvalue()


def test_composed_mask_save_does_not_reacquire_database_writer(services):
    install_catalogue(services)
    reviews = install_reviews(services)
    artifacts = services.extension("artifacts")
    owner = Caller("owner", "owner-test")
    target, _ = artifacts.upload(
        owner, "project-test", "target-image", image_bytes("RGB", "red"), "image/png"
    )
    mask, _ = artifacts.upload(
        owner, "project-test", "mask-image", image_bytes("L", 255), "image/png"
    )
    target_ref = dict(
        origin="reference",
        artifact_id=target["artifact_id"],
        displayed_sha256=target["sha256"],
        dimensions=target["dimensions"],
        item_id=None,
        version_id=None,
        shot_id=None,
        render_job_id=None,
        source_frame=None,
        media_artifact_id=None,
    )
    annotation = dict(
        annotation_id="annotation-mask",
        saved_revision=None,
        target=target_ref,
        range=None,
        camera=None,
        markups=[
            dict(
                id="mask-1",
                kind="mask",
                points=[[0, 0]],
                mask_artifact_id=mask["artifact_id"],
                mask_encoding=dict(
                    channel="luma",
                    bit_depth=8,
                    selected_value=255,
                    unselected_value=0,
                    semantics="binary",
                ),
            )
        ],
        text="Change the selected area",
        spatial_hits=[],
        spatial_status="unavailable",
    )
    receipt = reviews.save_annotation(
        owner,
        dict(
            schema_version="1.0",
            operation_id="save-mask",
            project_id="project-test",
            expected_revision=0,
            annotation=annotation,
        ),
    )
    assert receipt["state_revision"] == 1
    assert (
        artifacts.validate_mask(
            "project-test", "mask-image", dimensions=target["dimensions"], semantics="binary"
        )["artifact_id"]
        == "mask-image"
    )


def test_explicit_uploaded_mask_role_rejects_colour_reinterpretation(services):
    install_catalogue(services)
    artifacts = services.extension("artifacts")
    gamma = PngImagePlugin.PngInfo()
    gamma.add(b"gAMA", (45455).to_bytes(4, "big"))
    owner = Caller("owner", "owner-test")
    artifacts.upload(owner, "project-test", "gamma-mask", image_bytes("L", 128, gamma), "image/png")
    with pytest.raises(DomainError) as error:
        artifacts.validate_mask(
            "project-test", "gamma-mask", dimensions={"width": 8, "height": 8}, semantics="soft"
        )
    assert error.value.code == "invalid_request"
