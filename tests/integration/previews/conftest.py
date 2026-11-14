from dataclasses import replace

import pytest
from proscenium.auth import Caller
from proscenium.catalogue import install as install_catalogue
from proscenium.previews import install
from proscenium.previews.config import REVISION


def spec(digest="a" * 64, *, first=1, last=12, purpose="draft_motion", intent="intent-test"):
    result = dict(
        intent_id=intent,
        shot_id="shot-test",
        purpose=purpose,
        source_closure_digest=digest,
        scene_name="Fixture",
        camera=None,
        frames={"start": first, "end": last},
        fps={"numerator": 24, "denominator": 1},
        dimensions={"width": 640, "height": 360},
        engine="CYCLES",
        device="CPU",
        samples=8,
        colour_management={"view_transform": "Standard", "look": "None", "exposure": 0, "gamma": 1},
        renderer_revision=REVISION,
    )
    if purpose != "proxy":
        result["review_output"] = {
            "media_type": "image/png",
            "encoding": "view_transformed_srgb",
            "bit_depth": 8,
        }
    return result


@pytest.fixture
def previews(services):
    services.settings = replace(services.settings, execution_profiles=frozenset({"blender-cpu"}))
    services.jobs.settings = services.settings
    install_catalogue(services)
    services.gate.resume("project-test", reconciliation_evidence="Offline synthetic preview test")
    with services.registry.project_store("project-test").transaction() as tx:
        tx.put(
            "invocations",
            "invocation-test",
            {"work_id": "work-test", "owner_epoch": services.ownership.epoch, "state": "running"},
        )
        # Explicitly synthetic admission fixture, not evidence of native source resolution.
        tx.put(
            "versions",
            "version-test",
            {"version_id": "version-test", "item_id": "item-test", "source": {"digest": "a" * 64}},
        )
    return install(services)


@pytest.fixture
def preview_caller(previews):
    return Caller(
        "tool",
        "tool-test",
        "project-test",
        "work-test",
        previews.services.ownership.epoch,
        operations=("requestPreview", "retryToolJob"),
        invocation_id="invocation-test",
    )


@pytest.fixture
def request_body():
    return dict(
        schema_version="1.0",
        operation_id="operation-preview",
        project_id="project-test",
        version={"item_id": "item-test", "version_id": "version-test"},
        spec=spec(),
    )


def admit_synthetic(previews, caller, body):
    job = previews.request(caller, body)[0]
    root = previews.root(job)
    root.mkdir(parents=True, exist_ok=True)
    from proscenium.previews.journal import atomic

    atomic(root / "task.json", {key: job[key] for key in ("job_id", "attempt_id", "input_digest")})
    return job, root
