from copy import deepcopy
from fractions import Fraction

import pytest
from PIL import Image
from proscenium.api.errors import DomainError
from proscenium.previews.assembly import expand, linear_blend
from proscenium.previews.journal import atomic, sha
from proscenium.previews.service import worker_caller

from .conftest import admit_synthetic
from .test_publication import frame


def test_linear_rgb_dissolve_is_not_display_space_average():
    black, white = Image.new("RGB", (2, 2)), Image.new("RGB", (2, 2), "white")
    result = linear_blend([black, white], [Fraction(1, 2), Fraction(1, 2)])
    assert result.getpixel((0, 0)) == (188, 188, 188)
    assert linear_blend([white], [Fraction(1)]).tobytes() == white.tobytes()
    with pytest.raises(DomainError):
        linear_blend([black, white], [Fraction(1), Fraction(1)])


def test_identity_and_trim_expansion_keeps_exact_source_ordinals():
    sequence = dict(
        schema_version="1.0",
        project_id="p",
        sequence_version_id="sequence-v1",
        fps={"numerator": 24000, "denominator": 1001},
        frame_count=4,
        silent=True,
        entries=[
            dict(
                entry_id="cut",
                shot_id="shot",
                version_id="v1",
                job_id="job",
                source_frames={"start": 5, "end": 8},
                sequence_frames={"start": 1, "end": 4},
                transform="trim",
                mapping_artifact_id=None,
            )
        ],
    )
    mapping = expand(sequence, None)
    assert [r["contributors"][0]["source_frame"] for r in mapping["frames"]] == [5, 6, 7, 8]
    sequence["entries"][0]["transform"] = "retime"
    with pytest.raises(DomainError):
        expand(sequence, None)


def test_closed_gate_passive_journal_reconciliation_and_ambiguous_dispatch(
    previews, preview_caller, request_body
):
    job, root = admit_synthetic(previews, preview_caller, request_body)
    body = frame(job, root, 3)
    atomic(root / "frame-00000003.json", body)
    atomic(
        root / "terminal.json",
        {
            "state": "failed",
            "native_exit_code": 7,
            "task_sha256": sha((root / "task.json").read_bytes()),
        },
    )
    previews.services.gate.close("project-test")
    result = previews.reconcile("project-test")[0]
    assert result["state"] == "failed" and result["native_exit_code"] == 7
    assert previews.availability("project-test", job["job_id"])["available_ranges"] == [
        {"start": 3, "end": 3}
    ]
    assert not (root / "launch.claim").exists()


def test_late_old_attempt_cannot_mutate_retry(previews, preview_caller, request_body):
    job, root = admit_synthetic(previews, preview_caller, request_body)
    body = frame(job, root, 1)
    old_caller = worker_caller(job)
    previews.register(old_caller, body)
    terminal = previews.state(job, "failed", evidence="Synthetic verified native terminal")
    retried, _ = previews.services.jobs.retry(
        preview_caller,
        {
            "schema_version": "1.0",
            "operation_id": "retry-original",
            "project_id": "project-test",
            "job_id": job["job_id"],
            "expected_revision": terminal["state_revision"],
        },
    )
    assert retried["attempt_id"] != job["attempt_id"]
    assert previews.register(old_caller, body)[1]
    new_output = frame(job, root, 2)
    with pytest.raises(DomainError):
        previews.register(old_caller, new_output)
    assert (
        previews.services.jobs.get("project-test", job["job_id"])["attempt_id"]
        == retried["attempt_id"]
    )


def test_missing_segment_inputs_rejected_before_decode(previews, preview_caller, request_body):
    job, root = admit_synthetic(previews, preview_caller, request_body)
    (root / "segment.mp4").write_bytes(b"not a movie")
    body = dict(
        schema_version="1.0",
        segment_id="segment-test",
        project_id="project-test",
        job_id=job["job_id"],
        attempt_id=job["attempt_id"],
        shot_id="shot-test",
        version_id="version-test",
        source_frames={"start": 1, "end": 4},
        fps={"numerator": 24, "denominator": 1},
        frame_count=4,
        artifact_id="movie-test",
        output_path="segment.mp4",
        sha256=sha(b"not a movie"),
        input_digest=job["input_digest"],
    )
    with pytest.raises(DomainError) as error:
        previews.register(worker_caller(job), body, segment=True)
    assert error.value.code == "dependency_missing"


def test_frame_binding_never_tracks_new_viewport(previews, preview_caller, request_body):
    job, root = admit_synthetic(previews, preview_caller, request_body)
    altered = deepcopy(frame(job, root, 1))
    altered["version_id"] = "some-newer-version"
    with pytest.raises(DomainError) as error:
        previews.register(worker_caller(job), altered)
    assert error.value.code == "scope_denied"
