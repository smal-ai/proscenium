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
    probe = json.loads(
        run(
            [
                config["ffprobe"],
                "-v",
                "error",
                "-count_frames",
                "-show_streams",
                "-show_frames",
                "-of",
                "json",
                str(path),
            ],
            config["encode_timeout"],
        )
    )
    streams = probe["streams"]
    expected = Fraction(fps["numerator"], fps["denominator"])
    if len(streams) != 1:
        raise DomainError("media_unavailable", 422, "Video must be silent")
    stream = streams[0]
    frames = probe.get("frames", [])
    if (
        stream.get("codec_name") != "h264"
        or stream.get("pix_fmt") != "yuv420p"
        or stream.get("width") != dimensions["width"]
        or stream.get("height") != dimensions["height"]
        or int(stream.get("nb_read_frames", 0)) != count
        or Fraction(stream["avg_frame_rate"]) != expected
        or Fraction(stream["r_frame_rate"]) != expected
        or len(frames) != count
        or frames[0]["key_frame"] != 1
    ):
        raise DomainError("media_unavailable", 422, "Encoded video does not match admitted profile")
    pts = [
        Fraction(frame["best_effort_timestamp"]) * Fraction(stream["time_base"]) for frame in frames
    ]
    if pts != [Fraction(i) / expected for i in range(count)]:
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
    return {"stream": stream, "pts": [str(value) for value in pts]}
