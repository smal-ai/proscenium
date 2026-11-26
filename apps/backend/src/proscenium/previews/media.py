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

CLIP_AUDIO_RATE = 48_000


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


def audio_filters(
    tracks: list[dict[str, Any]],
    video_duration: Fraction,
    *,
    first_input: int = 1,
    sample_rate: int = 44_100,
    extra: tuple[str, ...] = (),
) -> str:
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
        filters.append(
            f"aformat=sample_fmts=fltp:sample_rates={sample_rate}:channel_layouts=stereo"
        )
        if delay_samples := round(start * sample_rate):
            filters.append(f"adelay={delay_samples}S:all=1")
        chains.append(f"[{index + first_input}:a]" + ",".join(filters) + f"[a{index}]")
    combined = "".join(f"[a{i}]" for i in range(len(tracks))) + "".join(extra)
    sources = len(tracks) + len(extra)
    if sources > 1:
        combined += f"amix=inputs={sources}:duration=longest:normalize=0,"
    chains.append(combined + f"apad,atrim=0:{float(video_duration):.6f},asetpts=PTS-STARTPTS[aout]")
    return ";".join(chains)


def audio_inputs(tracks: list[dict[str, Any]]) -> list[str]:
    args: list[str] = []
    for track in tracks:
        audio_format = AUDIO_DEMUXERS.get(track.get("media_type", ""))
        if audio_format is None:
            raise DomainError("unsupported_media_type", 415)
        args.extend(
            ("-protocol_whitelist", "file", "-f", audio_format[0], "-i", str(track["path"]))
        )
    return args


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
    audio_codec: list[str] = []
    maps = ["-an"]
    if audio_tracks:
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
        *audio_inputs(audio_tracks or []),
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
    run_encode(command, config["encode_timeout"], config["claim_path"])
    return command


def run_encode(command: list[str], timeout: int, claim_path: str | None) -> None:
    if claim_path is None:
        run(command, timeout)
        return
    claim = Path(claim_path).with_name("ffmpeg-encode.lock")
    claim.parent.mkdir(parents=True, exist_ok=True)
    with claim.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run(command, timeout)


def clip_canvas(dimensions: dict) -> dict:
    return {key: value + value % 2 for key, value in dimensions.items()}


def tempo_filters(ratio: Fraction) -> list[str]:
    if not Fraction(1, 240) <= ratio <= 240:
        raise DomainError(
            "invalid_frame_mapping", 422, "Clip frame rates diverge beyond supported bounds"
        )
    chain = []
    while ratio > 2:
        chain.append("atempo=2")
        ratio /= 2
    while ratio < Fraction(1, 2):
        chain.append("atempo=0.5")
        ratio *= 2
    if ratio != 1:
        chain.append(f"atempo={float(ratio):.10f}")
    return chain


def assemble_clips(
    clips: dict[str, dict],
    entries: list[dict],
    fps: dict,
    output_path: Path,
    *,
    silent: bool,
    preserve_clip_audio: bool,
    audio_tracks: list[dict[str, Any]],
    ffmpeg: str,
    ffprobe: str,
    timeout: int,
    claim_path: str | None = None,
) -> list[str]:
    """Trim and concatenate verified local clips on the exact sequence-frame timeline."""
    dimensions = clip_canvas(clips[entries[0]["clip_artifact_id"]]["dimensions"])
    width, height = dimensions["width"], dimensions["height"]
    rate = Fraction(fps["numerator"], fps["denominator"])
    rate_expr = f"{rate.numerator}/{rate.denominator}"
    timing = f"settb={rate.denominator}/{rate.numerator},setpts=N"
    keep_audio = not silent and preserve_clip_audio
    count = entries[-1]["sequence_frames"]["end"]
    argv = [ffmpeg, "-nostdin", "-v", "error", "-xerror", "-n", "-filter_complex_threads", "1"]
    chains, labels = [], []
    for index, entry in enumerate(entries):
        clip = clips[entry["clip_artifact_id"]]
        argv.extend(
            (
                "-threads",
                "1",
                "-protocol_whitelist",
                "file",
                "-f",
                "mov",
                "-enable_drefs",
                "0",
                "-i",
                str(clip["path"]),
            )
        )
        start, end = entry["source_frames"]["start"] - 1, entry["source_frames"]["end"]
        # Source and destination intervals have equal lengths. Conform cadence
        # frame-for-frame rather than allowing fps to silently drop/duplicate frames.
        chains.append(
            f"[{index}:v:0]trim=start_frame={start}:end_frame={end},setpts=PTS-STARTPTS,"
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p,"
            f"{timing},fps={rate_expr}[vclip{index}]"
        )
        labels.append(f"[vclip{index}]")
        if not keep_audio:
            continue
        sample_format = (
            f"aformat=sample_fmts=fltp:sample_rates={CLIP_AUDIO_RATE}:channel_layouts=stereo"
        )
        trimmed = float(Fraction(end - start) / rate)
        if entry.get("include_audio", True) and clip.get("channels") and clip.get("sample_rate_hz"):
            native_rate = Fraction(clip["fps"]["numerator"], clip["fps"]["denominator"])
            audio_chain = [
                f"[{index}:a:0]aresample={CLIP_AUDIO_RATE}:first_pts=0",
                f"atrim=start={float(start / native_rate):.9f}:end={float(end / native_rate):.9f}",
                "asetpts=PTS-STARTPTS",
                sample_format,
                *tempo_filters(rate / native_rate),
                "apad",
            ]
        else:
            audio_chain = [f"anullsrc=r={CLIP_AUDIO_RATE}:cl=stereo", sample_format]
        audio_chain.append(f"atrim=duration={trimmed:.9f},asetpts=PTS-STARTPTS[aclip{index}]")
        chains.append(",".join(audio_chain))
        labels.append(f"[aclip{index}]")

    chains.append(
        "".join(labels)
        + f"concat=n={len(entries)}:v=1:a={int(keep_audio)}[vcuts]"
        + ("[acuts]" if keep_audio else "")
    )
    chains.append(f"[vcuts]{timing}[vout]")
    maps = ["-map", "[vout]"]
    if silent:
        maps.append("-an")
    else:
        if not keep_audio and not audio_tracks:
            raise DomainError(
                "invalid_request", 422, "Non-silent sequences require an audio source"
            )
        argv.extend(audio_inputs(audio_tracks))
        chains.append(
            audio_filters(
                audio_tracks,
                Fraction(count) / rate,
                first_input=len(entries),
                sample_rate=CLIP_AUDIO_RATE,
                extra=("[acuts]",) if keep_audio else (),
            )
        )
        maps.extend(
            (
                "-map",
                "[aout]",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-ar",
                str(CLIP_AUDIO_RATE),
                "-ac",
                "2",
            )
        )
    command = [
        *argv,
        "-filter_complex",
        ";".join(chains),
        *maps,
        "-r",
        rate_expr,
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
        "-movflags",
        "+faststart",
        "-video_track_timescale",
        str(rate.numerator),
        str(output_path),
    ]
    run_encode(command, timeout, claim_path)
    verify_video(
        output_path,
        fps,
        count,
        dimensions,
        {"ffmpeg": ffmpeg, "ffprobe": ffprobe, "encode_timeout": timeout},
        expect_audio=not silent,
        audio_sample_rate=CLIP_AUDIO_RATE,
    )
    return command


def verify_video(
    path: Path,
    fps: dict,
    count: int,
    dimensions: dict,
    config: dict,
    *,
    expect_audio: bool = False,
    audio_sample_rate: int = 44_100,
) -> dict:
    return _verify_video(
        path,
        config,
        expected={"fps": fps, "frame_count": count, "dimensions": dimensions},
        expect_audio=expect_audio,
        audio_sample_rate=audio_sample_rate,
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
    audio_sample_rate: int = 44_100,
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
                or sample_rate != audio_sample_rate
                or int(audio_stream["channels"]) != 2
                or audio_duration <= 0
            ):
                raise ValueError
        except (KeyError, ValueError, TypeError, OverflowError) as exc:
            raise DomainError(
                "media_unavailable",
                422,
                f"Expected positive-duration stereo AAC at {audio_sample_rate} Hz",
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
