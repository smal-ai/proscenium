"""Fixed H.264 recipe and complete decode validation. No shell command strings."""

import fcntl
import json
import math
import os
import signal
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any

from proscenium.api.errors import DomainError
from proscenium.audio_formats import AUDIO_DEMUXERS


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


def audio_filters(tracks: list[dict[str, Any]], video_duration: Fraction) -> str:
    chains = []
    for index, track in enumerate(tracks):
        start = float(track["start_seconds"])
        trim_start = float(track.get("trim_start_seconds", 0))
        remaining = float(video_duration) - start
        duration = min(float(track.get("duration_seconds", remaining)), remaining)
        source_duration = track.get("source_duration_seconds")
        effective = (
            duration
            if source_duration is None
            else min(duration, max(0, float(source_duration) - trim_start))
        )
        filters = [f"atrim=start={trim_start:.6f}:duration={duration:.6f}", "asetpts=PTS-STARTPTS"]
        if "gain_db" in track:
            filters.append(f"volume={float(track['gain_db']):.4f}dB")
        if fade_in := float(track.get("fade_in_seconds", 0)):
            filters.append(f"afade=t=in:st=0:d={fade_in:.6f}")
        if fade_out := float(track.get("fade_out_seconds", 0)):
            filters.append(f"afade=t=out:st={max(0, effective - fade_out):.6f}:d={fade_out:.6f}")
        filters.append("aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo")
        if delay_samples := round(start * 44100):
            filters.append(f"adelay={delay_samples}S:all=1")
        chains.append(f"[{index + 1}:a]" + ",".join(filters) + f"[a{index}]")
    combined = "".join(f"[a{i}]" for i in range(len(tracks)))
    if len(tracks) > 1:
        combined += f"amix=inputs={len(tracks)}:duration=longest:normalize=0,"
    chains.append(combined + f"apad,atrim=0:{float(video_duration):.6f},asetpts=PTS-STARTPTS[aout]")
    return ";".join(chains)


def encode(
    root: Path,
    start: int,
    count: int,
    fps: dict,
    config: dict,
    target: Path,
    *,
    audio_tracks: list[dict[str, Any]] | None = None,
) -> list[str]:
    audio_inputs: list[str] = []
    audio_codec: list[str] = []
    maps = ["-an"]
    if audio_tracks:
        for track in audio_tracks:
            audio_format = AUDIO_DEMUXERS.get(track.get("media_type", ""))
            if audio_format is None:
                raise DomainError("unsupported_media_type", 415)
            audio_inputs.extend(
                ("-protocol_whitelist", "file", "-f", audio_format[0], "-i", str(track["path"]))
            )
        video_duration = Fraction(count, 1) / Fraction(fps["numerator"], fps["denominator"])
        maps = [
            "-filter_complex",
            audio_filters(audio_tracks, video_duration),
            "-map",
            "0:v:0",
            "-map",
            "[aout]",
        ]
        audio_codec = ["-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2"]
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
        *audio_inputs,
        "-frames:v",
        str(count),
        *maps,
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
        *audio_codec,
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


def verify_video(
    path: Path, fps: dict, count: int, dimensions: dict, config: dict, *, expect_audio: bool = False
) -> dict:
    return _verify_video(
        path,
        config,
        expected={"fps": fps, "frame_count": count, "dimensions": dimensions},
        expect_audio=expect_audio,
    )


def inspect_video(path: Path, config: dict, *, expected: dict) -> dict:
    """Verify retained MP4 bytes and return only contract artefact metadata."""
    return _verify_video(path, config, expected=expected, require_container_count=True)["metadata"]


def _verify_video(
    path: Path,
    config: dict,
    *,
    expected: dict,
    require_container_count: bool = False,
    expect_audio: bool = False,
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
    frames = probe.get("frames", [])
    if expect_audio:
        video = [stream for stream in streams if stream.get("codec_type") == "video"]
        audio = [stream for stream in streams if stream.get("codec_type") == "audio"]
        if len(streams) != 2 or len(video) != 1 or len(audio) != 1:
            raise DomainError("media_unavailable", 422, "Expected one video and one audio stream")
        stream = video[0]
        frames = [frame for frame in frames if frame.get("media_type") == "video"]
        audio_stream = audio[0]
        try:
            if any(
                type(audio_stream[key]) not in {str, int, float}
                for key in ("start_time", "duration")
            ):
                raise ValueError
            audio_start = float(audio_stream["start_time"])
            audio_duration = float(audio_stream["duration"])
            if not math.isfinite(audio_start) or not math.isfinite(audio_duration):
                raise ValueError
        except (KeyError, ValueError, TypeError, OverflowError) as exc:
            raise DomainError(
                "media_unavailable", 422, "Incomplete MP4 audio stream metadata"
            ) from exc
        try:
            sample_rate = int(audio_stream["sample_rate"])
            if (
                audio_stream.get("codec_name") != "aac"
                or sample_rate != 44100
                or int(audio_stream["channels"]) != 2
                or audio_duration <= 0
            ):
                raise ValueError
        except (KeyError, ValueError, TypeError, OverflowError) as exc:
            raise DomainError(
                "media_unavailable", 422, "Expected positive-duration stereo AAC at 44100 Hz"
            ) from exc
    else:
        if len(streams) != 1:
            raise DomainError("media_unavailable", 422, "Video must be silent")
        stream = streams[0]
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
    if expect_audio:
        tolerance = 2 * (1024 / sample_rate)
        video_duration = float(Fraction(count, 1) / fps)
        audio_end = audio_start + audio_duration
        if abs(audio_start) > tolerance or abs(audio_end - video_duration) > tolerance:
            raise DomainError("media_unavailable", 422, "Audio timeline does not match the video")
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
            *(["-map", "0:a:0"] if expect_audio else []),
            "-f",
            "null",
            "-",
        ],
        config["encode_timeout"],
    )
    return {"stream": stream, "pts": [str(value) for value in pts], "metadata": metadata}
