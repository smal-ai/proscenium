"""Still/motion policy and supervisor EOF checks. Child processes are Python doubles."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from proscenium.api.errors import DomainError
from proscenium.previews import worker
from proscenium.previews.config import Rendering
from proscenium.previews.journal import atomic, read, sha

from .conftest import spec


@pytest.mark.parametrize("width,height", [(1031, 569), (2063, 1138), (640, 360)])
def test_keyframe_admission_preserves_exact_intrinsic_dimensions(width, height):
    value = spec(first=1, last=1, purpose="keyframe")
    value["dimensions"] = {"width": width, "height": height}
    original = json.dumps(value, sort_keys=True)
    Rendering().validate(value)
    assert json.dumps(value, sort_keys=True) == original


@pytest.mark.parametrize("purpose", ["draft_motion", "final"])
@pytest.mark.parametrize("width,height", [(1031, 569), (2063, 1138), (640, 569)])
def test_motion_still_requires_even_pixels(purpose, width, height):
    value = spec(purpose=purpose)
    value["dimensions"] = {"width": width, "height": height}
    with pytest.raises(DomainError, match="H.264 motion requires even dimensions"):
        Rendering().validate(value)


@pytest.mark.parametrize(
    "width,height,expected_segments", [(1031, 569, 0), (2063, 1138, 0), (640, 360, 1)]
)
@pytest.mark.parametrize("complete", [True, False])
def test_supervisor_eof_for_exact_png_stills(
    tmp_path, monkeypatch, width, height, expected_segments, complete
):
    value = spec(first=1, last=1, purpose="keyframe")
    value["dimensions"] = {"width": width, "height": height}
    root = tmp_path / "owned-output"
    root.mkdir()
    atomic(
        root / "task.json",
        {
            "spec": value,
            "config": {
                "claim_path": str(tmp_path / "own.lock"),
                "blender": "not-launched",
                "threads": 4,
                "timeout": 5,
            },
            "entrypoint_sha256": sha(
                Path(worker.__file__).with_name("blender_worker.py").read_bytes()
            ),
        },
    )
    real_popen = subprocess.Popen
    native_calls = []
    encoded = []
    script = (
        "import pathlib,json,hashlib,sys; p=pathlib.Path(sys.argv[1]); "
        "(p/'frames').mkdir(); data=b'EXPLICIT OFFLINE FRAME DOUBLE'; "
        "(p/'frames/00000001.png').write_bytes(data); "
        "r={'output_path':'frames/00000001.png','sha256':hashlib.sha256(data).hexdigest()}; "
        + ("(p/'frame-00000001.json').write_text(json.dumps(r)); " if complete else "")
        + "print('offline child finished',flush=True)"
    )

    def python_double(argv, **kwargs):
        native_calls.append(argv)
        return real_popen([sys.executable, "-c", script, str(root)], **kwargs)

    def segment_double(root, task, first, last):
        encoded.append((first, last))
        return {"explicit_offline_encoder_double": True}

    monkeypatch.setattr(worker.subprocess, "Popen", python_double)
    monkeypatch.setattr(worker, "segment", segment_double)
    worker.supervise(root)
    terminal = read(root / "terminal.json")
    assert terminal["state"] == ("succeeded" if complete else "failed")
    assert terminal["native_exit_code"] == 0  # Observed Python double exit, not Blender evidence.
    assert len(native_calls) == 1 and len(encoded) == (expected_segments if complete else 0)
    assert not list(root.glob("*.mp4"))
    assert read(root / "task.json")["spec"]["dimensions"] == {"width": width, "height": height}


def test_odd_keyframe_ready_without_playable_video(previews, preview_caller, request_body):
    import io

    from PIL import Image
    from proscenium.previews.service import worker_caller

    request_body["spec"] = spec(first=1, last=1, purpose="keyframe")
    dimensions = {"width": 1031, "height": 569}
    request_body["spec"]["dimensions"] = dimensions
    job = previews.request(preview_caller, request_body)[0]
    root = previews.root(job)
    root.mkdir(parents=True)
    atomic(root / "task.json", {key: job[key] for key in ("job_id", "attempt_id", "input_digest")})
    pixels = io.BytesIO()
    Image.new("RGB", (1031, 569), (32, 96, 224)).save(pixels, format="PNG")
    (root / "odd.png").write_bytes(pixels.getvalue())
    receipt = dict(
        schema_version="1.0",
        receipt_id="odd-still-receipt",
        project_id=job["project_id"],
        job_id=job["job_id"],
        attempt_id=job["attempt_id"],
        version_id="version-test",
        shot_id="shot-test",
        source_frame=1,
        artifact_id="odd-still-artifact",
        output_path="odd.png",
        sha256=sha(pixels.getvalue()),
        dimensions=dimensions,
        input_digest=job["input_digest"],
    )
    previews.register(worker_caller(job), receipt)
    current = previews.services.jobs.get(job["project_id"], job["job_id"])
    current = previews.state(
        current, "succeeded", evidence="Explicit offline supervisor terminal double"
    )
    assert current["availability"] == "complete" and current["completed_frames"] == 1
    assert current["artifact_ids"] == ["odd-still-artifact"]
    availability = previews.availability(job["project_id"], job["job_id"])
    assert availability["available_ranges"] == [{"start": 1, "end": 1}]
    assert availability["playable_ranges"] == availability["pending_ranges"] == []
