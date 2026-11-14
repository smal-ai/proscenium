"""Read retained M08 journals/media/history. No SDK, browser, Blender or model startup."""

import argparse
import json
import re
import statistics
from pathlib import Path

from PIL import Image, ImageChops
from proscenium.contracts import validate
from proscenium.previews.journal import identity, read, sha
from proscenium.previews.media import verify_video


def main(root, export=False):
    state = read(root / "assembly-complete.json")
    recovery = read(root / "passive-recovery.json")
    checkpoint = read(root / "crash-checkpoint.json")
    killed = read(root / "backend-kill.json")
    fixture = read(root / "fixture.json")
    project = Path(fixture["project"])
    jobs = {job["render_spec"]["intent_id"]: job for job in state["jobs"] if job["render_spec"]}
    main_job, probe = jobs["m08-motion"], jobs["m08-recovery4"]

    def job_root(job):
        return project / ".proscenium/jobs/previews" / job["job_id"] / job["attempt_id"]

    artifact = {a["artifact_id"]: a for a in state["artifacts"]}

    def path(a):
        return project / ".proscenium/artefacts/sha256" / a["sha256"] / "content"

    annotation = read(root / "annotation-original.json")
    assert state["annotations"] == recovery["annotations"] == [annotation]
    frames = sorted(
        [f for f in state["frames"] if f["job_id"] == main_job["job_id"]],
        key=lambda f: f["source_frame"],
    )
    assert [f["source_frame"] for f in frames] == list(range(1, 13))
    for frame in state["frames"]:
        assert sha(path(artifact[frame["artifact_id"]]).read_bytes()) == frame["sha256"]
    timings = [read(job_root(main_job) / f"timing-{i:08d}.json") for i in range(1, 13)]
    start = read(job_root(main_job) / "process.json")["started_ns"]
    end = timings[-1]["ready_ns"]
    saved, played = read(root / "annotation-ready.json"), read(root / "first-segment-played.json")
    assert (
        saved["host_ns"] < played["host_ns"] < end
        and not saved["last_png_exists"]
        and not played["last_png_exists"]
    )
    expected_first_segment = next(
        s
        for s in state["segments"]
        if s["job_id"] == main_job["job_id"] and s["source_frames"] == {"start": 1, "end": 4}
    )
    assert annotation["target"]["media_artifact_id"] == expected_first_segment["artifact_id"]
    assert annotation["target"]["source_frame"] == 3 and annotation["range"] == {
        "start": 1,
        "end": 4,
    }
    assert annotation["target"]["displayed_sha256"] == frames[2]["sha256"]
    presentations = played["body"]["presentations"]
    first_presented_ordinals = [
        1 + int(row["mediaTime"] * 24 + 1e-4)
        for row in presentations
        if expected_first_segment["artifact_id"] in row["src"]
    ]
    assert {1, 2, 3, 4} <= set(first_presented_ordinals)
    interval_ms = [
        (b["now"] - a["now"])
        for a, b in zip(presentations, presentations[1:])
        if b["mediaTime"] > a["mediaTime"] and a["src"] == b["src"]
    ]
    durations = [(t["ready_ns"] - t["started_ns"]) / 1e9 for t in timings]
    for job in (main_job, probe):
        task = read(job_root(job) / "task.json")
        for file in task["closure"]["files"]:
            assert (
                sha((Path(task["source_root"]) / file["destination"]).read_bytes())
                == file["origin"]["sha256"]
            )
    # Actual decoded pixels of probe frames 2..4 were produced after the live
    # working texture changed. They must equal the original committed-blue job.
    pixel_equal = []
    for ordinal in range(1, 5):
        a = Image.open(job_root(main_job) / f"frames/{ordinal:08d}.png").convert("RGB")
        b = Image.open(job_root(probe) / f"frames/{ordinal:08d}.png").convert("RGB")
        equal = ImageChops.difference(a, b).getbbox() is None
        assert equal
        pixel_equal.append(equal)
    assert (
        checkpoint["live_texture_before"]
        != checkpoint["live_texture_after"]
        == sha((project / "assets/cube/colour.png").read_bytes())
    )
    recovered = next(j for j in recovery["jobs"] if j["job_id"] == probe["job_id"])
    assert recovered["attempt_id"] == checkpoint["job"]["attempt_id"] == probe["attempt_id"]
    assert recovered["input_digest"] == checkpoint["job"]["input_digest"] == probe["input_digest"]
    assert not recovery["gate_open"] and not recovery["sdk_object_created"]
    assert recovery["contexts"] == checkpoint["before"]["contexts"]
    assert recovery["publications"] == checkpoint["before"]["publications"]
    assert recovery["versions"] == checkpoint["before"]["versions"]
    probe_times = [read(job_root(probe) / f"timing-{i:08d}.json") for i in range(1, 5)]
    assert all(t["ready_ns"] > killed["host_ns"] for t in probe_times[1:])
    terminal = read(job_root(probe) / "terminal.json")
    assert (
        terminal["native_exit_code"] == 0 and terminal["task_sha256"] == checkpoint["task_sha256"]
    )
    assert sha((job_root(probe) / "task.json").read_bytes()) == checkpoint["task_sha256"]
    held = jobs["m08-held-output"]
    assert next(j for j in recovery["jobs"] if j["job_id"] == held["job_id"])["state"] == "planned"
    assert held["state"] == "cancelled" and not held.get("native_exit_code")
    assert not (job_root(held) / "launch.claim").exists()
    encode = next(j for j in state["jobs"] if j["job_id"] == "m08-final-encode")
    assert encode["state"] == "succeeded"
    movie = next(artifact[a] for a in encode["artifact_ids"] if artifact[a]["kind"] == "video")
    mapping = next(
        artifact[a] for a in encode["artifact_ids"] if artifact[a]["kind"] == "frame_map"
    )
    frame_map = read(path(mapping))
    validate("FrameMap", frame_map)
    assert [r["sequence_frame"] for r in frame_map["frames"]] == list(range(1, 13))
    for row in frame_map["frames"]:
        assert row["contributors"] == [
            {
                "job_id": main_job["job_id"],
                "shot_id": "m08-motion",
                "version_id": "m08-scene-v1",
                "source_frame": row["sequence_frame"],
                "weight": {"numerator": 1, "denominator": 1},
            }
        ]
    config = read(job_root(main_job) / "task.json")["config"]
    decoded = verify_video(
        path(movie), {"numerator": 24, "denominator": 1}, 12, {"width": 640, "height": 360}, config
    )
    for segment in state["segments"]:
        verify_video(
            path(artifact[segment["artifact_id"]]),
            segment["fps"],
            segment["frame_count"],
            {"width": 640, "height": 360},
            config,
        )
    pose = read(project / "scenes/m08-motion/readback.json")
    assert pose["samples"][0]["cube_location"] != pose["samples"][-1]["cube_location"]
    assert pose["samples"][0]["camera_to_world"] == timings[0]["camera_matrix_world"]
    assert pose["samples"][-1]["camera_to_world"] == timings[-1]["camera_matrix_world"]
    scene_version = next(v for v in state["versions"] if v["version_id"] == "m08-scene-v1")
    for f in scene_version["source"]["files"]:
        if f["destination"].endswith(".blend"):
            assert sha((project / f["destination"]).read_bytes()) == f["origin"]["sha256"]
    history = next((root / "machine/copilot-runtime/home/session-state").glob("*/events.jsonl"))
    native = [json.loads(line) for line in history.read_text().splitlines()]
    users = [
        e
        for e in native
        if e["type"] == "user.message" and not e["data"].get("isAutopilotContinuation")
    ]
    assert len(users) == 3 and not any(e["type"].startswith("subagent.") for e in native)
    identity_data = next(e["data"] for e in native if e["type"] == "session.start")
    author = read(next((project / ".proscenium/tool-output").glob("m08-author-[0-9].json")))
    assert author["native_exit_code"] == 0
    logs = [
        (job_root(j) / "blender.log").read_text()
        for j in jobs.values()
        if j["state"] == "succeeded"
    ]
    peak = max(float(value) for log in logs for value in re.findall(r"\(Peak ([0-9.]+)M\)", log))
    elapsed = (read(job_root(main_job) / "terminal.json")["finished_ns"] - start) / 1e9
    result = {
        "status": "targeted_progressive_and_worker_recovery_pass_dependencies_pending",
        "retained_root": str(root),
        "allocation": {
            "cloud_roots": 3,
            "native_children": 0,
            "authoring_processes": 1,
            "cpu_frames": 17,
            "proxy_exports": 1,
            "held_probe_rendered": False,
        },
        "runtime": {
            "sdk": "1.0.34",
            "cli": identity_data["copilotVersion"],
            "model": identity_data["selectedModel"],
            "session_id": identity_data["sessionId"],
            "blender": pose["blender"],
            "browser": "159.0.8154.6",
            "viewport": [1920, 1080, 1],
            "engine": "CYCLES",
            "device": "CPU",
            "samples": 8,
            "threads": 4,
            "dimensions": [640, 360],
            "fps": [24, 1],
        },
        "source": {
            "version": scene_version,
            "working_live_texture_sha256": checkpoint["live_texture_after"],
            "authoring_readback_sha256": sha(
                (project / "scenes/m08-motion/readback.json").read_bytes()
            ),
            "probe_pixels_equal_original_main_frames_1_4": pixel_equal,
        },
        "progressive": {
            "job": main_job,
            "annotation": annotation,
            "last_frame_ready_ns": end,
            "annotation_host_ns": saved["host_ns"],
            "first_segment_played_host_ns": played["host_ns"],
            "annotation_margin_seconds": (end - saved["host_ns"]) / 1e9,
            "playback_margin_seconds": (end - played["host_ns"]) / 1e9,
            "first_png_ready_seconds": (timings[0]["ready_ns"] - start) / 1e9,
            "first_segment_ready_seconds": (
                read(job_root(main_job) / "encode-00000001.json")["ready_ns"] - start
            )
            / 1e9,
            "render_total_seconds": elapsed,
            "throughput_fps": 12 / elapsed,
            "frame_median_seconds": statistics.median(durations),
            "frame_p95_nearest_rank_seconds": max(durations),
            "rvfc_order_with_intentional_pause_replay": first_presented_ordinals,
            "increasing_callback_intervals_ms": interval_ms,
            "renderer_reported_peak_M": peak,
            "os_rss_and_cpu_percent": "not sampled",
        },
        "recovery": {
            "job": probe,
            "backend_kill": killed,
            "native_pid_start": checkpoint["native"],
            "supervisor_pid_start": checkpoint["supervisor"],
            "task_sha256": checkpoint["task_sha256"],
            "postkill_frame_ready_seconds": [
                (t["ready_ns"] - killed["host_ns"]) / 1e9 for t in probe_times
            ],
            "native_exit_code": terminal["native_exit_code"],
            "closed_gate": not recovery["gate_open"],
            "no_sdk_object": not recovery["sdk_object_created"],
            "old_sdk_absent": identity(checkpoint["sdk_process"]["pid"]) is None,
            "same_attempt_input": True,
            "held_job": held,
            "same_contexts_publications_versions": True,
            "old_watermark": checkpoint["before"]["watermark"],
            "reconciled_watermark": recovery["watermark"],
        },
        "final_movie": {
            "job": encode,
            "video": movie,
            "frame_map_artifact": mapping,
            "frame_map": frame_map,
            "verified_stream": decoded["stream"],
            "actual_browser_readback": read(root / "actual-movie-readback.json"),
        },
        "actual_shot_cards": read(root / "actual-shot-cards.json"),
        "authoring_process": author,
        "frame_receipts": state["frames"],
        "segment_receipts": state["segments"],
        "cleanup": {
            phase: read(root / f"cleanup-{phase}.json")
            for phase in ("initial", "correction", "recovery", "assembly", "readback")
        },
        "files_sha256": {
            name: sha((root / name).read_bytes())
            for name in [
                "failure-initial.json",
                "failure-correction.json",
                "annotation-ready.json",
                "first-segment-played.json",
                "crash-checkpoint.json",
                "backend-kill.json",
                "passive-recovery.json",
                "assembly-complete.json",
                "browser-readback.json",
            ]
        },
    }
    result["m08_criteria"] = {
        "1": "PASS: actual compiled shot cards, model-authored canonical scene/camera/range/FPS bindings and real ready frame references",
        "2": "PASS: newly rendered source proxy, native lighting keyframe and progressive animation toggled in one viewer",
        "3": "PASS: actual saved PNG/rVFC-ordinal pin and first contiguous segment playback strictly before main frame 12",
        "4": "PASS: actual pending frame-12 seek, no old-version substitution; accepted W05 middle-hole cases remain dependencies",
        "5": "PASS: drawing paused playback; source-frame-3 clean PNG and segment identity with inclusive range 1..4 retained",
        "6": "PASS: mounted saved editor/pin DOM and snapshot unchanged across late output/restart/reload/final encode; no unsaved-edit conflict injected",
        "7": "PASS: fully decoded silent twelve-frame movie with exact same-job/version/shot/frame contributors and unchanged original annotation",
        "8": "PASS: actual first-ready/latency/throughput/callback intervals and bounded resource observations; not a universal benchmark",
        "9": "PASS WITH ACCEPTED DEPENDENCIES: W05 three-browser F03/F04 fractional codec/step/hole/cadence matrix not rerun; this actual Chromium journey restored the same job availability after restart",
    }
    result["limits"] = [
        "M04/M06 cumulative dependency acceptance and M08 milestone acceptance remain coordinator-owned.",
        "M12 worker slice only. No claim for whole native-history/provider/Unity/backup/project-switch recovery.",
        "Original twelve-frame crash guard refused the shell-launcher-to-Blender exec argv transition; no crash was induced there. Four-frame probe used the fixed worker's PID/start corroboration.",
        "Four-frame probe uses the same published source/profile but a declared new intent/range and distinct input digest. Its own exact attempt/input digest stayed unchanged through restart.",
        "Two acknowledged harness admissions initially retained running invocation flags. Only those proven harness calls were settled; the refused Resume record and all SDK/permission history remain retained.",
        "Resume actions are disclosed owner API calls with fresh project revisions, not a UI Resume pass.",
        "Animation annotations name the receipted clean PNG corresponding to an rVFC ordinal and segment; not lossless-video-identical pixels.",
        "Intentional pause/annotation/replay gives repeated rVFC ordinals. No uninterrupted all-twelve-source-frame-once browser claim is substituted for accepted F03/F04 tests.",
        "Opening reference keyframe and main motion both use allocated CPU8samples640x360; no claim that the draft was measured cheaper than this tiny keyframe.",
    ]
    write_path = root / "final-measurements.json"
    write_path.write_text(json.dumps(result, indent=2) + "\n")
    if export:
        destination = Path(__file__).resolve().parents[3] / "docs/evidence/m08/final-run.json"
        destination.write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                "status": result["status"],
                "allocation": result["allocation"],
                "annotation_margin_seconds": result["progressive"]["annotation_margin_seconds"],
                "playback_margin_seconds": result["progressive"]["playback_margin_seconds"],
                "source_probe_pixel_equality": pixel_equal,
                "movie_sha256": movie["sha256"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument(
        "--export",
        action="store_true",
        help="Write the explicit public-metadata/measurement allowlist to owned evidence",
    )
    args = parser.parse_args()
    main(args.root.resolve(strict=True), args.export)
