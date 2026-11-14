"""Deterministic attachment-role checks, not another native camera/SDK proof."""

import copy
import json
from pathlib import Path

import pytest
from test_contexts import annotation, image
from test_delivery import PROJECT
from test_delivery import runtime as runtime


@pytest.mark.parametrize("position", ["primary", "annotation", "finding"])
def test_proxy_capture_keeps_one_reference_file_and_original_markup_masks(runtime, position):
    target, _ = image(runtime, "inspection-image")
    image(runtime, "inspection-markup")
    image(runtime, "inspection-mask", mode="L", colour=0)
    target.update(
        origin="proxy_capture",
        item_id="scene-inspection",
        version_id="version-inspection",
        source_frame=1,
    )
    context = json.loads(Path("docs/contracts/examples/context.json").read_text())
    context.update(
        project_id=PROJECT,
        attachments=[
            {
                "artifact_id": "inspection-image",
                "role": "reference",
                "use": "Original clean inspection capture",
            },
            {"artifact_id": "inspection-markup", "role": "markup"},
            {"artifact_id": "inspection-mask", "role": "mask"},
        ],
    )
    marked = annotation(target)
    marked["markup_artifact_id"] = "inspection-markup"
    marked["markups"] = [
        dict(
            id="inspection-mask-markup",
            kind="mask",
            points=[[0.1, 0.1]],
            mask_artifact_id="inspection-mask",
            mask_encoding=dict(
                channel="luma",
                bit_depth=8,
                selected_value=255,
                unselected_value=0,
                semantics="binary",
            ),
        )
    ]
    if position == "primary":
        context["target"] = target
    elif position == "annotation":
        context["annotations"] = [marked]
    else:
        context["approved_findings"] = [
            dict(
                finding_id="selected-finding",
                finding_revision=1,
                approved_text="Selected finding for this target",
                target=target,
            )
        ]
    original = copy.deepcopy(context)
    with runtime.services.registry.project_store(PROJECT).transaction(write=False) as tx:
        files = runtime.contexts.files(tx, context)
    assert [(f["artifact_id"], f["role"]) for f in files] == [
        ("inspection-image", "reference"),
        ("inspection-markup", "markup"),
        ("inspection-mask", "mask"),
    ]
    assert context == original  # No target, snapshot or explicit semantic attachment is rewritten.


@pytest.mark.parametrize(
    "origin,expected",
    [
        ("proxy_capture", ["reference", "render"]),
        ("blender_render", ["reference", "render"]),
        ("sequence_render", ["reference", "render"]),
    ],
)
def test_automatic_roles_preserve_intentional_same_image_different_semantics(
    runtime, origin, expected
):
    target, _ = image(runtime, "same-image")
    target["origin"] = origin
    context = dict(
        attachments=[
            {"artifact_id": "same-image", "role": "reference"},
            {"artifact_id": "same-image", "role": "render"},
        ],
        target=target,
        annotations=[],
        approved_findings=[],
    )
    original = copy.deepcopy(context)
    with runtime.services.registry.project_store(PROJECT).transaction(write=False) as tx:
        files = runtime.contexts.files(tx, context)
    assert [f["role"] for f in files] == expected
    assert context == original


@pytest.mark.parametrize("origin", ["blender_render", "sequence_render"])
def test_real_render_origins_still_add_render_without_relabelling_the_reference(runtime, origin):
    target, _ = image(runtime, "render-image")
    target["origin"] = origin
    context = dict(
        attachments=[{"artifact_id": "render-image", "role": "reference"}],
        target=target,
        annotations=[],
        approved_findings=[],
    )
    with runtime.services.registry.project_store(PROJECT).transaction(write=False) as tx:
        files = runtime.contexts.files(tx, context)
    assert [f["role"] for f in files] == ["reference", "render"]
