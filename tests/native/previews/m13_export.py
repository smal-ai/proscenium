"""Verify retained M13 effects and export only non-secret evidence. No model/encode."""

import hashlib
import importlib.metadata
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

from proscenium.contracts import canonical, validate
from proscenium.previews.assembly import technical

REPO = Path(__file__).resolve().parents[3]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read(path):
    return json.loads(path.read_text())


def main():
    root = Path(sys.argv[1]).resolve()
    output = REPO / "docs/evidence/m13"
    output.mkdir(parents=True, exist_ok=True)
    project = root / "project"
    imported = read(root / "import.json")
    source = Path(imported["source"])
    final = read(root / "public-final.json")
    before = read(root / "public-before-owner-selection.json")
    reviewed = read(root / "public-review-v1.json")
    arrived = read(root / "public-v2-arrived-view-pinned.json")
    restored = read(root / "public-restored-v1.json")
    connection = sqlite3.connect(f"file:{project / '.proscenium/state.sqlite3'}?mode=ro", uri=True)

    def rows(namespace):
        return [
            json.loads(row[0])
            for row in connection.execute(
                "SELECT body FROM records WHERE namespace=? ORDER BY id", (namespace,)
            )
        ]

    def get(namespace, identity):
        return json.loads(
            connection.execute(
                "SELECT body FROM records WHERE namespace=? AND id=?", (namespace, identity)
            ).fetchone()[0]
        )

    jobs = [get("jobs", f"m13-encode-v{i}") for i in (1, 2)]
    render_records = [j for j in rows("jobs") if j["kind"] == "render"]
    assert len(render_records) == len(imported["original_jobs"]) == 2
    for original in imported["original_jobs"]:
        assert get("jobs", original["job_id"]) == {
            **original,
            "project_id": "m13-cinematic",
            "work_id": "work-fixture-import",
        }
    versions = {v["version_id"]: v for v in final["versions"]["m13-film"]["items"]}
    assert len(versions) == 2 and len(rows("requests")) == 2
    assert all(r["receipt"]["state"] == "completed" for r in rows("requests"))
    continuations = rows("copilot.continuation_sends")
    assert len(continuations) == 1 and continuations[0]["state"] == "acknowledged"
    usages = [
        json.loads(row[0])["payload"]
        for row in connection.execute(
            "SELECT body FROM events WHERE json_extract(body,'$.event_type')='usage.recorded'"
        )
    ]
    with (REPO / "node_modules/@github/copilot-linux-x64/copilot").open("rb") as binary:
        cli_hash = hashlib.file_digest(binary, "sha256").hexdigest()
    assert not [r for r in rows("invocations") if r.get("kind") == "child"]
    assert len(read(root / "encode-dispatches.json")) == 2
    replay = read(root / "replay.json")
    assert (
        replay["result"]["job_id"] == jobs[0]["job_id"]
        and replay["dispatches"] == 1
        and replay["job_count"] == 3
    )
    assert (
        read(root / "cleanup.json")["services_closed"]
        and read(root / "cleanup.json")["browser_reaped"]
    )
    preservation = []
    for row in imported["hashes"]:
        if "artifact_id" in row:
            old = source / f".proscenium/artefacts/sha256/{row['sha256']}/content"
            new = project / f".proscenium/artefacts/sha256/{row['sha256']}/content"
            assert sha(old.read_bytes()) == sha(new.read_bytes()) == row["sha256"]
        else:
            origin = next(
                f["origin"]
                for f in imported["original_version"]["source"]["files"]
                if f["destination"] == row["source_path"]
            )
            blob = subprocess.run(
                ["git", "show", f"{origin['commit']}:{origin['path']}"],
                cwd=source,
                check=True,
                capture_output=True,
            ).stdout
            if blob.startswith(b"version https://git-lfs.github.com/spec/v1"):
                digest = origin["sha256"]
                blob = (
                    source / f".git/lfs/objects/{digest[:2]}/{digest[2:4]}/{digest}"
                ).read_bytes()
            assert sha(blob) == row["sha256"]
        preservation.append(row)
    edits = []
    for number, job in enumerate(jobs, 1):
        assert job["state"] == "succeeded" and job["availability"] == "complete"
        worker_root = project / f".proscenium/jobs/previews/{job['job_id']}/{job['attempt_id']}"
        terminal = read(worker_root / "terminal.json")
        process = read(worker_root / "process.json")
        assert (
            terminal["state"] == "succeeded" and terminal["task_sha256"] == process["task_sha256"]
        )
        assert (terminal["finished_ns"] - process["started_ns"]) / 1e9 < 60
        version = versions[f"m13-film-v{number}"]
        validate("VersionRecord", version)
        authored = {}
        for file in version["source"]["files"]:
            data = subprocess.run(
                ["git", "show", f"{file['origin']['commit']}:{file['origin']['path']}"],
                cwd=project,
                check=True,
                capture_output=True,
            ).stdout
            assert sha(data) == file["origin"]["sha256"]
            authored[file["destination"]] = json.loads(data)
        sequence = authored[version["source"]["entry_path"]]
        validate("SequenceMap", sequence)
        mapping = get("artifacts", job["job_id"] + "-frame-map")
        map_bytes = (
            project / f".proscenium/artefacts/sha256/{mapping['sha256']}/content"
        ).read_bytes()
        frame_map = json.loads(map_bytes)
        validate("FrameMap", frame_map)
        assert sha(map_bytes) == mapping["sha256"]
        delivery = get("artifacts", job["job_id"] + "-delivery")
        manifest = get("artifacts", job["job_id"] + "-manifest")
        downloads = read(root / f"downloads-v{number}.json")
        for downloaded, artifact in zip(downloads, (delivery, manifest), strict=True):
            assert (
                sha(Path(downloaded["path"]).read_bytes())
                == downloaded["sha256"]
                == artifact["sha256"]
            )
            assert downloaded["href"].endswith(f"/artifacts/{artifact['artifact_id']}/content")
        metadata = read(Path(downloads[1]["path"]))
        assert metadata["sequence"] == technical(sequence)
        assert (
            metadata["video_sha256"] == delivery["sha256"]
            and metadata["frame_map_sha256"] == mapping["sha256"]
        )
        snapshot = dict(
            sequence=metadata["sequence"],
            frame_map=frame_map,
            input_frames=metadata["input_frames"],
        )
        assert sha(canonical(snapshot)) == job["input_digest"] == metadata["input_digest"]
        for frame in metadata["input_frames"]:
            artifact = get("artifacts", frame["artifact_id"])
            assert artifact["sha256"] == frame["sha256"]
            assert (
                sha(
                    (
                        project / f".proscenium/artefacts/sha256/{artifact['sha256']}/content"
                    ).read_bytes()
                )
                == frame["sha256"]
            )
        video = Path(downloads[0]["path"])
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-count_frames",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(video),
            ],
            check=True,
            capture_output=True,
            timeout=10,
        )
        measured = json.loads(probe.stdout)
        assert len(measured["streams"]) == 1 and measured["streams"][0]["codec_type"] == "video"
        stream = measured["streams"][0]
        count = 10 if number == 1 else 8
        assert (
            int(stream["nb_read_frames"])
            == count
            == frame_map["frame_count"]
            == sequence["frame_count"]
        )
        assert stream["avg_frame_rate"] == "24/1" and (stream["width"], stream["height"]) == (
            640,
            360,
        )
        assert abs(float(measured["format"]["duration"]) - count / 24) < 0.00001
        decoded = subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-i", str(video), "-f", "null", "-"],
            capture_output=True,
            timeout=10,
        )
        assert decoded.returncode == 0 and not decoded.stderr
        playback = read(root / f"playback-v{number}.json")
        ordinals = [1 + round(f["mediaTime"] * 24) for f in playback]
        assert ordinals == list(range(1, count + 1))
        assert all((f["width"], f["height"]) == (640, 360) for f in playback)
        edits.append(
            dict(
                version=version,
                job=job,
                sequence=sequence,
                frame_map=frame_map,
                artifacts=[delivery, mapping, manifest],
                worker_terminal=terminal,
                worker_process=process,
                historical_native_exit_code=terminal["native_exit_code"],
                manifest=metadata,
                downloads=downloads,
                probe=measured,
                full_decode_exit=decoded.returncode,
                playback=playback,
            )
        )
    assert edits[0]["version"]["source_commit"] != edits[1]["version"]["source_commit"]
    assert edits[0]["artifacts"][0]["sha256"] != edits[1]["artifacts"][0]["sha256"]
    assert before["selections"]["items"] == []
    assert reviewed["selections"] == final["selections"] == restored["selections"]
    assert reviewed["annotations"] == final["annotations"] == restored["annotations"]
    annotation = final["annotations"]["items"][0]
    validate("AnnotationSnapshot", annotation)
    assert annotation["target"]["sequence_version_id"] == "m13-film-v1"
    assert (
        annotation["target"]["sequence_frame"] == 5 and annotation["target"]["source_frame"] is None
    )
    assert annotation["target"]["frame_map_artifact_id"] == "m13-encode-v1-frame-map"
    assert (
        annotation["range"] == {"start": 4, "end": 6} and annotation["markups"][0]["kind"] == "pin"
    )
    assert len(edits[0]["frame_map"]["frames"][4]["contributors"]) == 2
    assert (
        arrived["view"]["version"]["version_id"]
        == restored["view"]["version"]["version_id"]
        == "m13-film-v1"
    )
    assert final["view"]["version"]["version_id"] == "m13-film-v2"
    selection = final["selections"]["items"][0]
    assert (
        selection["actor"]["kind"] == "user"
        and selection["choice"]["version"]["version_id"] == "m13-film-v1"
    )
    assert final["viewport"] == dict(width=1920, height=1080, dpr=1)
    for file in (
        "annotation-v1.png",
        "v2-arrived-view-pinned.png",
        "v2-preserved-v1.png",
        "restored-v1.png",
        "review-failure.png",
    ):
        shutil.copyfile(root / file, output / file)
    durations = [
        read(root / "root-1.json")["elapsed_ms"],
        read(root / "resume-result.json")["elapsed_ms"],
        read(root / "root-2.json")["elapsed_ms"],
    ]
    assert len(durations) == 3 and max(durations) < 180000
    result = dict(
        status="PASS",
        scope="allocated M13 native/UI journey; cumulative dependency acceptance remains parent-owned",
        base_revision="e3cddf8",
        retained_root=str(root),
        source_preservation=preservation,
        fixture_import=imported,
        sdk_version=importlib.metadata.version("github-copilot-sdk"),
        model="gpt-6.1-sol",
        native_association=get("copilot.session", "native"),
        cli_version=read(REPO / "node_modules/@github/copilot/package.json")["version"],
        cli_binary_sha256=cli_hash,
        reported_models=sorted({u["model_id"] for u in usages if u.get("model_id")}),
        observed_usage=usages,
        ffmpeg_version=subprocess.run(
            ["ffmpeg", "-version"], check=True, capture_output=True, text=True, timeout=5
        ).stdout.splitlines()[0],
        cloud_dispatches=3,
        dispatch_durations_ms=durations,
        children=0,
        encode_dispatches=read(root / "encode-dispatches.json"),
        requests=rows("requests"),
        continuations=continuations,
        publications=[
            p
            for p in rows("publications")
            if p["receipt"]["publication_id"] != "m13-labelled-fixture-import"
        ],
        workflow_invocations=[
            i for i in rows("invocations") if i.get("name") == "assemble_sequence"
        ],
        edits=edits,
        imported_render_records=render_records,
        replay=replay,
        annotation=annotation,
        owner_selection=selection,
        before_owner_selection=before["selections"],
        after_publication_selection=final["selections"],
        annotations_before_revision=reviewed["annotations"],
        annotations_after_revision=final["annotations"],
        view_pinned_after_v2=arrived["view"]["version"]["version_id"],
        restored_view=restored["view"],
        browser=final["browser"],
        viewport=final["viewport"],
        cleanup=read(root / "cleanup.json"),
        operating_limits=dict(
            no_new_renders=True,
            no_gpu=True,
            no_model_service_lifecycle=True,
            headroom_observations_mib=[15797, 12949],
            continuous_headroom_minimum_measured=False,
        ),
        errors_and_fixes=[
            "Harness import initially lacked PYTHONPATH; no root or encode consumed.",
            "Original fixture project.json was untracked. Created an explicit new project identity retaining source repository IDs.",
            "Mutable Git working-tree bytes differed from pinned v1. Used W02 historical materialisation and verified original pinned Git/LFS bytes instead.",
            "Harness server background timeout interrupted cloud dispatch 1 after successful source checkpoint. Retained uncertainty; explicit same-work Resume consumed dispatch 2. No prompt replay or database reset.",
            "Initial exact-boundary seek to ordinal 5 presented ordinal 4. Corrected the UI to seek inside the sample interval, then verified actual rVFC frame 5 and all playback ordinals.",
            "Skipped inapplicable render-progress queries for complete encode jobs. No synthetic render availability substituted.",
        ],
        criteria={str(n): {"status": "PASS"} for n in range(1, 8)},
        dependencies_accepted_here=[],
    )
    (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    connection.close()
    print(
        json.dumps(
            {
                "status": "PASS",
                "cloud_dispatches": 3,
                "encodes": 2,
                "annotation": annotation["annotation_id"],
                "selection": selection["selection_id"],
            }
        )
    )


if __name__ == "__main__":
    main()
