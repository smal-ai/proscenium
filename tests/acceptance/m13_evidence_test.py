"""Read retained actual M13 evidence. No inference, encoding or browser launch."""

import hashlib
import json
from pathlib import Path

from PIL import Image
from proscenium.contracts import canonical, validate
from proscenium.previews.assembly import technical

EVIDENCE = Path(__file__).resolve().parents[2] / "docs/evidence/m13"


def result():
    return json.loads((EVIDENCE / "result.json").read_text())


def retained(identity):
    path = EVIDENCE / "retained" / identity
    assert hashlib.sha256(path.read_bytes()).hexdigest() == identity
    return path


def test_m13_c1_exact_pinned_shots_timing_and_fixture_provenance():
    r = result()
    assert r["fixture_import"]["native_history_imported"] is False
    assert len(r["imported_render_records"]) == 2
    for edit in r["edits"]:
        validate("SequenceMap", edit["sequence"])
        validate("FrameMap", edit["frame_map"])
        assert edit["manifest"]["sequence"] == technical(edit["sequence"])
        assert edit["frame_map"]["frame_count"] == edit["sequence"]["frame_count"]
        snapshot = dict(
            sequence=technical(edit["sequence"]),
            frame_map=edit["frame_map"],
            input_frames=edit["manifest"]["input_frames"],
        )
        assert hashlib.sha256(canonical(snapshot)).hexdigest() == edit["job"]["input_digest"]
        for frame in edit["manifest"]["input_frames"]:
            retained(frame["sha256"])


def test_m13_c2_complete_decode_fps_count_dimensions_and_no_resize():
    for edit, count in zip(result()["edits"], (10, 8), strict=True):
        assert edit["full_decode_exit"] == 0
        stream = edit["probe"]["streams"][0]
        assert (stream["width"], stream["height"]) == (640, 360)
        assert stream["avg_frame_rate"] == "24/1" and int(stream["nb_read_frames"]) == count
        assert abs(float(edit["probe"]["format"]["duration"]) - count / 24) < 0.00001
        assert edit["downloads"][0]["sha256"] == edit["artifacts"][0]["sha256"]
        retained(edit["downloads"][0]["sha256"])
        assert "-vf" not in edit["manifest"]["encode_argv"]
        assert edit["job"]["availability"] == "complete" and edit["job"]["state"] == "succeeded"


def test_m13_c3_actual_fullhd_playback_composite_capture_pin_and_range():
    r = result()
    assert r["viewport"] == {"width": 1920, "height": 1080, "dpr": 1}
    for edit in r["edits"]:
        assert [1 + round(f["mediaTime"] * 24) for f in edit["playback"]] == list(
            range(1, edit["frame_map"]["frame_count"] + 1)
        )
    annotation = r["annotation"]
    assert (
        annotation["target"]["source_frame"] is None and annotation["target"]["sequence_frame"] == 5
    )
    assert annotation["range"] == {"start": 4, "end": 6}
    assert annotation["markups"][0]["kind"] == "pin"
    assert len(r["edits"][0]["frame_map"]["frames"][4]["contributors"]) == 2
    image = retained(annotation["target"]["displayed_sha256"])
    with Image.open(image) as actual:
        assert actual.size == (640, 360)
    with Image.open(EVIDENCE / "annotation-v1.png") as screenshot:
        assert screenshot.size == (1920, 1080)


def test_m13_c4_all_deliveries_are_silent():
    for edit in result()["edits"]:
        assert "-an" in edit["manifest"]["encode_argv"]
        assert all(stream["codec_type"] != "audio" for stream in edit["probe"]["streams"])


def test_m13_c5_new_edit_preserves_old_annotated_version_mapping_and_choice():
    r = result()
    first, second = r["edits"]
    for key in ("version_id", "source_commit"):
        assert first["version"][key] != second["version"][key]
    assert first["artifacts"][0]["artifact_id"] != second["artifacts"][0]["artifact_id"]
    assert first["artifacts"][0]["sha256"] != second["artifacts"][0]["sha256"]
    assert [entry["entry_id"] for entry in first["sequence"]["entries"]] == [
        "opening",
        "blend",
        "arrival",
    ]
    assert [entry["entry_id"] for entry in second["sequence"]["entries"]] == ["arrival", "opening"]
    assert r["annotations_before_revision"] == r["annotations_after_revision"]
    assert r["view_pinned_after_v2"] == r["restored_view"]["version"]["version_id"] == "m13-film-v1"
    assert r["annotation"]["target"]["media_artifact_id"] == first["artifacts"][0]["artifact_id"]
    assert (
        r["annotation"]["target"]["frame_map_artifact_id"] == first["artifacts"][1]["artifact_id"]
    )


def test_m13_c6_actual_ui_downloads_trace_registered_media_manifest_and_sources():
    r = result()
    for edit in r["edits"]:
        assert [d["name"] for d in edit["downloads"]] == [
            "Download final video",
            "Download delivery manifest",
        ]
        for download, artifact in zip(
            edit["downloads"], (edit["artifacts"][0], edit["artifacts"][2]), strict=True
        ):
            assert artifact["sha256"] == download["sha256"]
            retained(download["sha256"])
            assert download["href"].endswith(f"/artifacts/{artifact['artifact_id']}/content")
        assert edit["manifest"]["video_sha256"] == edit["artifacts"][0]["sha256"]
        assert edit["manifest"]["frame_map_sha256"] == edit["artifacts"][1]["sha256"]
        for file in edit["version"]["source"]["files"]:
            retained(file["origin"]["sha256"])
    assert r["replay"]["job_count"] == 3 and r["replay"]["dispatches"] == 1
    assert len(r["encode_dispatches"]) == 2


def test_m13_c7_only_explicit_owner_choice_selects_final_delivery():
    r = result()
    assert r["before_owner_selection"]["items"] == []
    selection = r["owner_selection"]
    validate("SelectionRecord", selection)
    assert selection["actor"]["kind"] == "user" and selection["state"] == "selected"
    assert selection["choice"]["version"]["version_id"] == "m13-film-v1"
    assert r["after_publication_selection"]["items"] == [selection]
    assert r["cloud_dispatches"] == 3 and r["children"] == 0
    assert max(r["dispatch_durations_ms"]) < 180000
    assert r["cleanup"]["services_closed"] and r["cleanup"]["browser_reaped"]
    assert r["dependencies_accepted_here"] == []


def test_portable_bytes_keep_original_report_and_every_content_hash():
    manifest = json.loads((EVIDENCE / "retained-bytes.json").read_bytes())
    assert (
        hashlib.sha256((EVIDENCE / "result.json").read_bytes()).hexdigest()
        == manifest["source_report_sha256"]
    )
    for identity, metadata in manifest["files"].items():
        assert retained(identity).stat().st_size == metadata["bytes"]
        assert metadata["record_pointers"]
