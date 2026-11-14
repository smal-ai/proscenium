"""Bounded E03 native proof and deterministic F03/F04 media generator/checker.

No product imports, source saves, model services or desktop applications.
Generated media and logs stay in a fresh /tmp/opencode/proscenium-e03-* directory.
"""

import argparse
import concurrent.futures
import fcntl
import hashlib
import json
import math
import os
import platform
import selectors
import shutil
import signal
import statistics
import subprocess
import sys
import time
from datetime import datetime
from fractions import Fraction
from pathlib import Path
from zoneinfo import ZoneInfo

import PIL
from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 640, 360
SOURCE_PATHS = ("assets/cube/asset.blend", "assets/cube/colour.png", "scenes/fixture/scene.blend")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def run(command, timeout=60):
    """Only terminate our own process group if the bounded command times out."""
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          start_new_session=True) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise
        if process.returncode:
            raise RuntimeError(f"{command!r}: {process.returncode}\n{stderr.decode()[-4000:]}")
        return stdout


def encode(root, family, start, fps):
    folder = root / family
    output = folder / f"segment-{start:02d}-{start + 3:02d}.mp4"
    pending = output.with_suffix(".pending.mp4")
    # Separate encodes, an initial IDR, closed GOP and no B frames. No concat dependency.
    command = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-n",
               "-framerate", str(fps), "-start_number", str(start),
               "-i", str(folder / "frames/%04d.png"), "-frames:v", "4", "-an",
               "-c:v", "libx264", "-threads", "2", "-preset", "veryfast", "-crf", "18",
               "-pix_fmt", "yuv420p", "-g", "4", "-keyint_min", "4", "-sc_threshold", "0",
               "-bf", "0", "-flags", "+cgop", "-movflags", "+faststart",
               "-video_track_timescale", str(fps.numerator), str(pending)]
    started = time.monotonic_ns()
    run(command)
    pending.replace(output)
    sha256 = digest(output)
    ready = time.monotonic_ns()
    last_exists = (folder / "frames/0012.png").exists()
    receipt = {
        "family": family, "path": str(output.relative_to(root)), "sha256": sha256,
        "source_interval": [start, start + 3], "frame_count": 4,
        "fps": [fps.numerator, fps.denominator],
        "frame_centres": [str(Fraction(2 * i + 1, 2) / fps) for i in range(4)],
        "started_ns": started, "ready_ns": ready, "encode_seconds": (ready - started) / 1e9,
        "last_frame_existed_at_ready": last_exists, "command": command,
    }
    write_json(output.with_suffix(".json"), receipt)
    return receipt


def numbered_frame(ordinal):
    image = Image.new("RGB", (WIDTH, HEIGHT),
                      ((ordinal * 17) % 192 + 32, (ordinal * 37) % 192 + 32, 96))
    draw = ImageDraw.Draw(image)
    draw.rectangle((16, 16, 304, 96), fill="black")
    for bit in range(4):
        x = 32 + bit * 64
        draw.rectangle((x, 32, x + 47, 79), fill="white" if ordinal & (1 << bit) else "black")
    draw.text((320, 220), f"{ordinal:02d}", font=ImageFont.load_default(size=120),
              fill="white", stroke_width=2, stroke_fill="black", anchor="mm")
    draw.text((320, 330), "SYNTHETIC ORDINAL FIXTURE", font=ImageFont.load_default(size=20),
              fill="white", anchor="mm")
    return image


def ordinal_from_rgb(data):
    ordinal = 0
    for bit in range(4):
        offset = (56 * WIDTH + 56 + bit * 64) * 3
        rgb = data[offset:offset + 3]
        assert max(rgb) - min(rgb) < 20, f"Non-grey ordinal cell: {list(rgb)}"
        assert max(rgb) < 32 or min(rgb) > 223, f"Ambiguous ordinal cell: {list(rgb)}"
        if min(rgb) > 223:
            ordinal |= 1 << bit
    return ordinal


def validate_segment(root, receipt, numbered=False):
    path = root / receipt["path"]
    assert digest(path) == receipt["sha256"]
    probe_command = ["ffprobe", "-v", "error", "-count_frames", "-show_streams",
                     "-show_frames", "-of", "json", str(path)]
    probe = json.loads(run(probe_command))
    assert len(probe["streams"]) == 1, "Must be silent video only"
    stream = probe["streams"][0]
    fps = Fraction(*receipt["fps"])
    assert stream["codec_name"] == "h264" and stream["pix_fmt"] == "yuv420p"
    assert [stream["width"], stream["height"]] == [WIDTH, HEIGHT]
    assert int(stream["nb_read_frames"]) == 4
    assert Fraction(stream["r_frame_rate"]) == fps
    assert Fraction(stream["avg_frame_rate"]) == fps
    frames = probe["frames"]
    assert len(frames) == 4 and frames[0]["key_frame"] == 1
    pts = [Fraction(frame["best_effort_timestamp"]) * Fraction(stream["time_base"])
           for frame in frames]
    assert pts == [Fraction(i) / fps for i in range(4)]
    decode_command = ["ffmpeg", "-nostdin", "-v", "error", "-xerror", "-i", str(path),
                      "-map", "0:v:0", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
    data = run(decode_command)
    frame_bytes = WIDTH * HEIGHT * 3
    assert len(data) == 4 * frame_bytes
    decoded = [data[i * frame_bytes:(i + 1) * frame_bytes] for i in range(4)]
    ordinals = [ordinal_from_rgb(frame) for frame in decoded] if numbered else None
    if numbered:
        assert ordinals == list(range(receipt["source_interval"][0],
                                      receipt["source_interval"][1] + 1))
    return {
        "path": receipt["path"], "status": "decoded_verified", "probe_command": probe_command,
        "decode_command": decode_command, "stream": stream, "source_ordinals": ordinals,
        "pts_exact": [str(value) for value in pts],
        "decoded_rgb_sha256": [hashlib.sha256(frame).hexdigest() for frame in decoded],
    }


def native(root, source):
    original = {name: digest(source / name) for name in SOURCE_PATHS}
    fixture_record = json.loads((source.parent / "fixture-evidence.json").read_text())
    assert original == {row["path"]: row["sha256"] for row in fixture_record["historical_files"]}
    for name in SOURCE_PATHS:
        destination = root / "source" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, destination)
        destination.chmod(0o444)
    write_json(root / "source-identity.json", {
        "original_root": str(source), "files": original,
        "fixture_commit": fixture_record["asset_v1_commit"],
        "fixture_evidence_sha256": digest(source.parent / "fixture-evidence.json"),
    })
    folder = root / "native/frames"
    folder.mkdir(parents=True)
    write_json(root / "job.json", {"camera": None, "source_frames": [1, 12], "fps": [24, 1]})
    worker = Path(__file__).with_name("render_fixture.py").resolve()
    command = ["blender", "--background", "--factory-startup", "--disable-autoexec",
               "--threads", "4", "--python-exit-code", "1", "--python", str(worker),
               "--", str(root)]
    claim = Path("/tmp/opencode/proscenium-native-claims/blender-render.lock")
    claim.parent.mkdir(parents=True, exist_ok=True)
    frames, futures = [], []
    # The OS-held claim covers the complete owned Blender process lifetime.
    with claim.open("a+") as lock, concurrent.futures.ThreadPoolExecutor(max_workers=1) as encoder:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        started = time.monotonic_ns()
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              start_new_session=True) as process, (root / "blender.log").open("wb") as log:
            selector = selectors.DefaultSelector()
            selector.register(process.stdout, selectors.EVENT_READ)
            buffer = b""
            try:
                while True:
                    remaining = 300 - (time.monotonic_ns() - started) / 1e9
                    if remaining <= 0:
                        raise TimeoutError("Owned Blender exceeded 300 seconds")
                    if not selector.select(timeout=remaining):
                        raise TimeoutError("Owned Blender exceeded 300 seconds")
                    chunk = os.read(process.stdout.fileno(), 65536)
                    if not chunk:
                        break
                    log.write(chunk)
                    log.flush()
                    buffer += chunk
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        if not line.startswith(b"E03_RECEIPT "):
                            continue
                        receipt = json.loads(line[len(b"E03_RECEIPT "):])
                        receipt["observed_ns"] = time.monotonic_ns()
                        assert digest(root / receipt["path"]) == receipt["sha256"]
                        receipt["verified_ns"] = time.monotonic_ns()
                        frames.append(receipt)
                        if receipt["source_frame"] % 4 == 0:
                            futures.append(encoder.submit(encode, root, "native",
                                                          receipt["source_frame"] - 3,
                                                          Fraction(24, 1)))
                assert process.wait(timeout=max(0.01, remaining)) == 0
            except BaseException:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                raise
            finally:
                selector.close()
        segments = [future.result() for future in futures]
    elapsed = (time.monotonic_ns() - started) / 1e9
    assert [frame["source_frame"] for frame in frames] == list(range(1, 13))
    assert not segments[0]["last_frame_existed_at_ready"]
    assert segments[0]["ready_ns"] < frames[-1]["ready_ns"]
    assert frames[0]["camera_matrix_world"] != frames[-1]["camera_matrix_world"]
    assert frames[0]["moving_cube_location"] != frames[-1]["moving_cube_location"]
    assert all(digest(source / name) == sha == digest(root / "source" / name)
               for name, sha in original.items())
    render_times = [(frame["ready_ns"] - frame["render_started_ns"]) / 1e9 for frame in frames]
    report = {
        "status": "native_bounded_progressive_pass", "command": command, "claim": str(claim),
        "started_ns": started, "elapsed_seconds_including_final_encode": elapsed,
        "first_verified_frame_seconds": (frames[0]["verified_ns"] - started) / 1e9,
        "first_ready_segment_seconds": (segments[0]["ready_ns"] - started) / 1e9,
        "last_frame_seconds": (frames[-1]["ready_ns"] - started) / 1e9,
        "frame_seconds": render_times, "frame_seconds_median": statistics.median(render_times),
        "frame_seconds_p95_nearest_rank": sorted(render_times)[math.ceil(0.95 * 12) - 1],
        "frames_per_second_including_startup_and_encode": 12 / elapsed,
        "source_unchanged": True, "frames": frames, "segments": segments,
    }
    write_json(root / "native-result.json", report)
    return report


def synthetic(root):
    segments = []
    for family, fps in (("f03", Fraction(24, 1)), ("f04", Fraction(24000, 1001))):
        folder = root / family / "frames"
        folder.mkdir(parents=True)
        frame_receipts = []
        for ordinal in range(1, 13):
            target = folder / f"{ordinal:04d}.png"
            numbered_frame(ordinal).save(target, compress_level=9)
            frame_receipts.append({"source_frame": ordinal, "path": str(target.relative_to(root)),
                                   "sha256": digest(target)})
        for start in (1, 5, 9):
            segments.append(encode(root, family, start, fps))
        write_json(root / family / "fixture.json", {
            "status": "synthetic_numbered_derivative", "fps": [fps.numerator, fps.denominator],
            "frames": frame_receipts, "segments": [s for s in segments if s["family"] == family],
            "ordinal_oracle": "4 little-endian black/white bits at centres x=56+64*bit,y=56",
            "provenance": "Pillow RGB generator, NOT native render output", "pillow": PIL.__version__,
        })
    return segments


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", type=Path,
                        default=Path("/tmp/opencode/proscenium-f01-20261114-a/historical"))
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    root = args.output.resolve()
    assert root.parent == Path("/tmp/opencode") and root.name.startswith("proscenium-e03-")
    if args.check_only:
        result = json.loads((root / "result.json").read_text())
        for segment in result["segments"]:
            validate_segment(root, segment, segment["family"] != "native")
        for family in ("native", "f03", "f04"):
            for path in (root / family / "frames").glob("*.json"):
                row = json.loads(path.read_text())
                assert digest(root / row["path"]) == row["sha256"]
        print("Existing media hashes, metadata, full decode and synthetic ordinals verified")
        return
    if root.exists() and any(root.iterdir()):
        raise ValueError("Output must be empty. Use --check-only to inspect an existing proof.")
    root.mkdir(exist_ok=True)
    native_result = native(root, args.source.resolve())
    segments = native_result["segments"] + synthetic(root)
    checks = [validate_segment(root, segment, segment["family"] != "native")
              for segment in segments]
    result = {
        "scope": "E03 native progressive feasibility plus synthetic F03/F04 codec fixtures only",
        "recorded_at": datetime.now(ZoneInfo("Africa/Johannesburg")).isoformat(),
        "base_commit": run(["git", "rev-parse", "HEAD"]).decode().strip(),
        "system": platform.uname()._asdict(), "python": sys.version, "pillow": PIL.__version__,
        "ffmpeg": run(["ffmpeg", "-version"]).decode(),
        "ffprobe": run(["ffprobe", "-version"]).decode(),
        "worker_sha256": digest(Path(__file__).with_name("render_fixture.py")),
        "generator_sha256": digest(Path(__file__)),
        "segments": segments, "checks": checks,
        "limitations": ["No product renderer/player, persistence, HTTP capability or recovery proof",
                        "No uninterrupted playback or missing-interval/annotation behaviour proof"],
    }
    write_json(root / "result.json", result)
    print(json.dumps({"output": str(root), "status": "bounded_proof_pass",
                      "first_frame_seconds": native_result["first_verified_frame_seconds"],
                      "first_segment_seconds": native_result["first_ready_segment_seconds"]}))


if __name__ == "__main__":
    main()
