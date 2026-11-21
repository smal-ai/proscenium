"""Fixed H.264 recipe and complete decode validation. No shell command strings."""

import fcntl
import json
import os
import signal
import subprocess
from fractions import Fraction
from pathlib import Path

from proscenium.api.errors import DomainError


def run(argv: list[str], timeout: int = 60) -> bytes:
    with subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True
    ) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise
        if process.returncode:
            raise DomainError(
                "media_unavailable",
                422,
                "Bounded media command failed",
                native_exit_code=process.returncode,
            )
        return stdout


def encode(root: Path, start: int, count: int, fps: dict, config: dict, target: Path) -> list[str]:
    command = [
        config["ffmpeg"],
        "-nostdin",
        "-v",
        "error",
        "-n",
        "-framerate",
        f"{fps['numerator']}/{fps['denominator']}",
        "-start_number",
        str(start),
        "-i",
        str(root / "frames/%08d.png"),
        "-frames:v",
        str(count),
        "-an",
        "-c:v",
        "libx264",
        "-threads",
        "2",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-g",
        str(count),
        "-keyint_min",
        str(count),
        "-sc_threshold",
        "0",
        "-bf",
        "0",
        "-flags",
        "+cgop",
        "-movflags",
        "+faststart",
        "-video_track_timescale",
        str(fps["numerator"]),
        str(target),
    ]
    claim = Path(config["claim_path"]).with_name("ffmpeg-encode.lock")
    claim.parent.mkdir(parents=True, exist_ok=True)
    with claim.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run(command, config["encode_timeout"])
    return command


def verify_video(path: Path, fps: dict, count: int, dimensions: dict, config: dict) -> dict:
    return _verify_video(
        path, config, expected={"fps": fps, "frame_count": count, "dimensions": dimensions}
    )


def inspect_video(path: Path, config: dict, *, expected: dict) -> dict:
    """Verify retained MP4 bytes and return only contract artefact metadata."""
    return _verify_video(path, config, expected=expected, require_container_count=True)["metadata"]


def _verify_video(
    path: Path, config: dict, *, expected: dict, require_container_count: bool = False
) -> dict:
    probe = json.loads(
        run(
            [
                config["ffprobe"],
                "-v",
                "error",
                "-count_frames",
                "-show_streams",
                "-show_frames",
                "-show_format",
                "-of",
                "json",
                str(path),
            ],
            config["encode_timeout"],
        )
    )
    streams = probe.get("streams", [])
    container = probe.get("format", {})
    if "mp4" not in container.get("format_name", "").split(",") or container.get("tags", {}).get(
        "major_brand"
    ) in {None, "qt  "}:
        raise DomainError("media_unavailable", 422, "Expected an MP4 container")
    if len(streams) != 1:
        raise DomainError("media_unavailable", 422, "Video must be silent")
    stream = streams[0]
    frames = probe.get("frames", [])
    try:
        fps = Fraction(stream["avg_frame_rate"])
        nominal_fps = Fraction(stream["r_frame_rate"])
        count = int(stream["nb_read_frames"])
        width, height = stream["width"], stream["height"]
        container_count = int(stream["nb_frames"]) if require_container_count else count
        time_base = Fraction(stream["time_base"])
    except (KeyError, ValueError, TypeError, ZeroDivisionError) as exc:
        raise DomainError("media_unavailable", 422, "Incomplete MP4 stream metadata") from exc
    metadata = {
        "dimensions": {"width": width, "height": height},
        "fps": {"numerator": fps.numerator, "denominator": fps.denominator},
        "frame_count": count,
    }
    if (
        stream.get("codec_type") != "video"
        or stream.get("codec_name") != "h264"
        or stream.get("pix_fmt") != "yuv420p"
        or any(type(value) is not int or value < 2 or value % 2 for value in (width, height))
        or fps <= 0
        or nominal_fps != fps
        or time_base <= 0
        or count <= 0
        or container_count != count
        or len(frames) != count
        or frames[0].get("key_frame") != 1
        or any(metadata[key] != value for key, value in expected.items())
    ):
        raise DomainError("media_unavailable", 422, "Encoded video does not match admitted profile")
    try:
        pts = [Fraction(frame["best_effort_timestamp"]) * time_base for frame in frames]
    except (KeyError, ValueError, TypeError, ZeroDivisionError) as exc:
        raise DomainError("invalid_frame_mapping", 422) from exc
    if any(value != Fraction(i) / fps for i, value in enumerate(pts)):
        raise DomainError("invalid_frame_mapping", 422)
    # Null output forces complete decode without retaining an unbounded raw RGB movie in memory.
    run(
        [
            config["ffmpeg"],
            "-nostdin",
            "-v",
            "error",
            "-xerror",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-f",
            "null",
            "-",
        ],
        config["encode_timeout"],
    )
    return {"stream": stream, "pts": [str(value) for value in pts], "metadata": metadata}
