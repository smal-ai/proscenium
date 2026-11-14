"""Deterministic, bounded CPU-only safetensors F32 -> F16 derivative conversion.

No torch, CUDA, model initialisation, download or inference. CLI is one exact owned file.
NumPy is supplied by the already-installed Comfy Python environment, not a global install.
"""

import argparse
import hashlib
import json
import math
import os
import resource
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path.home() / ".cache/proscenium-development/image-generation-light"
SOURCE = ROOT / "models/checkpoints/sd_turbo.safetensors"
DESTINATION = ROOT / "models/checkpoints/sd_turbo-proscenium-fp16-1.safetensors"
SOURCE_SHA256 = "3f067a1b943cf162f2b8f8588f6cf5824bd5b4c7d1d88d87164b9ca123616549"
CHUNK_BYTES = 4 * 1024**2
RSS_LIMIT = 1024**3
HEADROOM = 4 * 1024**3
RTOL = 2.0**-11
ATOL = 2.0**-25
ITEM_BYTES = {
    "BOOL": 1,
    "U8": 1,
    "I8": 1,
    "U16": 2,
    "I16": 2,
    "U32": 4,
    "I32": 4,
    "U64": 8,
    "I64": 8,
    "F16": 2,
    "BF16": 2,
    "F32": 4,
    "F64": 8,
    "F8_E4M3": 1,
    "F8_E5M2": 1,
    "F8_E4M3FNUZ": 1,
    "F8_E5M2FNUZ": 1,
    "F8_E8M0": 1,
}


class Guard:
    def __init__(self) -> None:
        self.started = time.monotonic()
        self.minimum_available: int | None = None
        self.check()

    def check(self) -> None:
        if "torch" in sys.modules:
            raise RuntimeError("Torch import is prohibited in this CPU conversion")
        if time.monotonic() - self.started >= 300:
            raise TimeoutError("300-second conversion bound reached")
        # Linux ru_maxrss can retain the launcher's pre-exec high-water value.
        # VmHWM/VmRSS here describe THIS exec's actual mappings, not the parent.
        if process_peak_rss() >= RSS_LIMIT:
            raise MemoryError("One-GiB converter peak-RSS bound exceeded")
        values = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
        available = int(values["MemAvailable"].split()[0]) * 1024
        self.minimum_available = min(self.minimum_available or available, available)
        if available < HEADROOM:
            raise MemoryError("Four-GiB protected host headroom unavailable")


def process_peak_rss() -> int:
    status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines())
    return max(int(status[name].split()[0]) * 1024 for name in ("VmRSS", "VmHWM"))


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate JSON/header/tensor key")
        value[key] = item
    return value


def layout(path: Path) -> tuple[dict[str, Any], int, list[str]]:
    with path.open("rb") as stream:
        prefix = stream.read(8)
        if len(prefix) != 8:
            raise ValueError("Incomplete safetensors prefix")
        length = int.from_bytes(prefix, "little")
        if not 0 < length <= 32 * 1024**2 or length % 8:
            raise ValueError("Invalid bounded/padded safetensors header")
        raw = stream.read(length)
    if len(raw) != length:
        raise ValueError("Incomplete safetensors header")
    header = json.loads(raw, object_pairs_hook=unique_object)
    if not isinstance(header, dict):
        raise ValueError("Header must be an object")
    metadata = header.get("__metadata__", {})
    if not isinstance(metadata, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in metadata.items()
    ):
        raise ValueError("Safetensors metadata must contain only string pairs")
    names = [name for name in header if name != "__metadata__"]
    for name in names:
        tensor = header[name]
        if not isinstance(tensor, dict) or set(tensor) != {"dtype", "shape", "data_offsets"}:
            raise ValueError("Invalid tensor definition")
        shape, offsets, dtype = tensor["shape"], tensor["data_offsets"], tensor["dtype"]
        if (
            dtype not in ITEM_BYTES
            or not isinstance(shape, list)
            or any(type(n) is not int or n < 0 for n in shape)
            or not isinstance(offsets, list)
            or len(offsets) != 2
            or any(type(n) is not int or n < 0 for n in offsets)
            or offsets[1] < offsets[0]
            or offsets[1] - offsets[0] != math.prod(shape) * ITEM_BYTES[dtype]
        ):
            raise ValueError("Tensor dtype/shape/offset byte count mismatch")
    names.sort(
        key=lambda name: (header[name]["data_offsets"][0], header[name]["data_offsets"][1], name)
    )
    cursor = 0
    for name in names:
        begin, end = header[name]["data_offsets"]
        if begin != cursor:
            raise ValueError("Tensor payload has a gap, overlap or alias")
        cursor = end
    if not names or 8 + length + cursor != path.stat().st_size:
        raise ValueError("Header does not cover the complete file payload")
    return header, 8 + length, names


def digest(path: Path, guard) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        while raw := stream.read(CHUNK_BYTES):
            guard.check()
            result.update(raw)
    return result.hexdigest()


def precision(source, half) -> dict:
    if not np.all(np.isfinite(source)) or np.any(np.abs(source) > np.finfo(np.float16).max):
        raise ValueError("Nonfinite or out-of-range F32 value; no clamping permitted")
    if not np.all(np.isfinite(half)):
        raise ValueError("Conversion introduced a nonfinite value")
    exact = source.astype(np.float64)
    difference = np.abs(exact - half.astype(np.float64))
    tolerance = np.abs(exact) * RTOL + ATOL
    if np.any(difference > tolerance):
        raise ValueError("Converted chunk exceeds round-to-nearest F16 error bound")
    normal = np.abs(exact) >= 2.0**-14
    normal_relative = (
        float(np.max(difference[normal] / np.abs(exact[normal]))) if np.any(normal) else 0.0
    )
    return dict(
        max_abs_error=float(difference.max(initial=0)),
        max_normal_relative_error=normal_relative,
        rounded_nonzero_to_zero=int(np.count_nonzero((source != 0) & (half == 0))),
    )


def convert_file(source: Path, destination: Path, expected_source_sha256: str, guard) -> dict:
    guard.check()
    if source.is_symlink() or destination.exists() or destination.is_symlink():
        raise FileExistsError("Source must be regular and destination must be NEW, never overwrite")
    if (
        source.parent.resolve() != source.parent
        or destination.parent.resolve() != destination.parent
    ):
        raise ValueError("Conversion must not traverse symlinked model directories")
    if digest(source, guard) != expected_source_sha256:
        raise ValueError("Original checkpoint SHA-256 mismatch")
    original_stat = source.stat()
    header, start, names = layout(source)
    output_header = {}
    if "__metadata__" in header:
        output_header["__metadata__"] = header["__metadata__"]
    cursor, converted_elements, source_f32_bytes, preserved_bytes = 0, 0, 0, 0
    for name in names:
        tensor = header[name]
        count = tensor["data_offsets"][1] - tensor["data_offsets"][0]
        converting = tensor["dtype"] == "F32"
        size = count // 2 if converting else count
        output_header[name] = {
            "dtype": "F16" if converting else tensor["dtype"],
            "shape": tensor["shape"],
            "data_offsets": [cursor, cursor + size],
        }
        cursor += size
        if converting:
            converted_elements += count // 4
            source_f32_bytes += count
        else:
            preserved_bytes += count
    raw_header = json.dumps(
        output_header, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    raw_header += b" " * (-len(raw_header) % 8)
    partial = destination.with_suffix(destination.suffix + ".partial")
    statistics = dict(max_abs_error=0.0, max_normal_relative_error=0.0, rounded_nonzero_to_zero=0)
    chunks = 0
    # Fail before large work if the local CPU casting mode is not ties-to-even.
    tie = np.array([1 + 2.0**-11, 1 + 3 * 2.0**-11], dtype="<f4").astype("<f2")
    if not np.array_equal(tie.astype("<f4"), np.array([1, 1 + 2.0**-9], dtype="<f4")):
        raise ValueError("CPU half conversion is not the expected nearest/ties-even mode")
    with source.open("rb") as input_stream, partial.open("xb") as output_stream:
        output_stream.write(len(raw_header).to_bytes(8, "little"))
        output_stream.write(raw_header)
        for name in names:
            tensor = header[name]
            begin, end = tensor["data_offsets"]
            input_stream.seek(start + begin)
            remaining = end - begin
            while remaining:
                guard.check()
                size = min(remaining, CHUNK_BYTES)
                raw = input_stream.read(size)
                if len(raw) != size:
                    raise ValueError("Unexpected truncated tensor body")
                if tensor["dtype"] == "F32":
                    values = np.frombuffer(raw, dtype="<f4")
                    if not np.all(np.isfinite(values)) or np.any(
                        np.abs(values) > np.finfo(np.float16).max
                    ):
                        raise ValueError("Nonfinite/overflow source chunk; no derivative published")
                    with np.errstate(over="raise", invalid="raise", under="ignore"):
                        half = values.astype("<f2")
                    check = precision(values, half)
                    statistics["max_abs_error"] = max(
                        statistics["max_abs_error"], check["max_abs_error"]
                    )
                    statistics["max_normal_relative_error"] = max(
                        statistics["max_normal_relative_error"], check["max_normal_relative_error"]
                    )
                    statistics["rounded_nonzero_to_zero"] += check["rounded_nonzero_to_zero"]
                    output_stream.write(half.tobytes())
                else:
                    output_stream.write(raw)
                remaining -= size
                chunks += 1
                guard.check()
        output_stream.flush()
        os.fsync(output_stream.fileno())
    # Full independent streaming read-back: exact coverage, metadata/names/shapes,
    # finite rounding on EVERY F32-derived chunk, and bit-exact other dtype bodies.
    converted_header, converted_start, converted_names = layout(partial)
    if set(converted_names) != set(names) or converted_header.get("__metadata__") != header.get(
        "__metadata__"
    ):
        raise ValueError("Tensor names/metadata changed")
    with source.open("rb") as original, partial.open("rb") as converted:
        for name in names:
            a, b = header[name], converted_header[name]
            if a["shape"] != b["shape"] or b["dtype"] != (
                "F16" if a["dtype"] == "F32" else a["dtype"]
            ):
                raise ValueError("Tensor shape/dtype read-back mismatch")
            original.seek(start + a["data_offsets"][0])
            converted.seek(converted_start + b["data_offsets"][0])
            remaining = a["data_offsets"][1] - a["data_offsets"][0]
            while remaining:
                guard.check()
                size = min(remaining, CHUNK_BYTES)
                first = original.read(size)
                second = converted.read(size // 2 if a["dtype"] == "F32" else size)
                if len(first) != size or len(second) != (
                    size // 2 if a["dtype"] == "F32" else size
                ):
                    raise ValueError("Read-back body truncated")
                if a["dtype"] == "F32":
                    values = np.frombuffer(first, dtype="<f4")
                    half = np.frombuffer(second, dtype="<f2")
                    precision(values, half)
                    if values.astype("<f2").tobytes() != second:
                        raise ValueError("Read-back differs from exact nearest/ties-even CPU cast")
                elif first != second:
                    raise ValueError("Preserved dtype body changed")
                remaining -= size
    output_sha = digest(partial, guard)
    if digest(source, guard) != expected_source_sha256:
        raise ValueError("Original checkpoint changed during conversion")
    now = source.stat()
    if (now.st_ino, now.st_size, now.st_mtime_ns, now.st_mode) != (
        original_stat.st_ino,
        original_stat.st_size,
        original_stat.st_mtime_ns,
        original_stat.st_mode,
    ):
        raise ValueError("Original checkpoint identity/permissions changed")
    expected_bytes = 8 + len(raw_header) + source_f32_bytes // 2 + preserved_bytes
    if partial.stat().st_size != expected_bytes:
        raise ValueError("Derivative byte count mismatch")
    guard.check()
    partial.chmod(0o400)
    os.link(partial, destination)
    partial.unlink()
    return dict(
        source_sha256=expected_source_sha256,
        source_bytes=original_stat.st_size,
        output_sha256=output_sha,
        output_bytes=expected_bytes,
        output_filename=destination.name,
        tensor_count=len(names),
        converted_tensor_count=sum(header[n]["dtype"] == "F32" for n in names),
        converted_elements=converted_elements,
        source_f32_payload_bytes=source_f32_bytes,
        derivative_f16_payload_bytes=source_f32_bytes // 2,
        preserved_other_payload_bytes=preserved_bytes,
        output_header_bytes=len(raw_header),
        metadata_preserved=True,
        complete_offset_coverage=True,
        names_shapes_sha256=hashlib.sha256(
            json.dumps(
                {name: header[name]["shape"] for name in sorted(names)},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest(),
        full_streaming_readback_validated=True,
        original_hash_unchanged_after=True,
        chunk_bytes=CHUNK_BYTES,
        converted_chunks=chunks,
        rounding="IEEE binary16 round-to-nearest ties-to-even; no clipping/clamping",
        relative_tolerance=RTOL,
        absolute_tolerance=ATOL,
        **statistics,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--convert-owned-sd-turbo", action="store_true", required=True)
    parser.parse_args()
    guard = Guard()
    report_path = ROOT / "sd-turbo-fp16-conversion-1.json"
    started_path = ROOT / "sd-turbo-fp16-conversion-1.started.json"
    if (
        started_path.exists()
        or report_path.exists()
        or DESTINATION.exists()
        or DESTINATION.with_suffix(DESTINATION.suffix + ".partial").exists()
    ):
        raise SystemExit(
            "Original conversion/partial already exists. Inspect it, never repeat or overwrite this allocation."
        )
    with started_path.open("x") as output:
        json.dump(
            {
                "source_sha256": SOURCE_SHA256,
                "converter_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "numpy_version": np.__version__,
                "seconds_max": 300,
                "rss_limit_bytes": RSS_LIMIT,
                "protected_host_bytes": HEADROOM,
                "cpu_only": True,
                "started_epoch": time.time(),
            },
            output,
            indent=2,
        )
        output.write("\n")
    if SOURCE.stat().st_mode & 0o222:
        raise ValueError("The preserved official checkpoint must remain read-only")
    result = convert_file(SOURCE, DESTINATION, SOURCE_SHA256, guard)
    result.update(
        numpy_version=np.__version__,
        python_version=sys.version.split()[0],
        torch_imported="torch" in sys.modules,
        converter_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        elapsed_seconds=time.monotonic() - guard.started,
        peak_rss_bytes=process_peak_rss(),
        rss_measurement="Linux /proc/self/status VmHWM/VmRSS for this exec; excludes inherited pre-exec getrusage high-water",
        inherited_ru_maxrss_counter_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
        minimum_host_mem_available_bytes=guard.minimum_available,
        source_repository="stabilityai/sd-turbo",
        source_revision="b261bac6fd2cf515557d5d0707481eafa0485ec2",
        derivative_is_not_official_download=True,
        licence="Stability AI Community License, 2024-07-05; derivative precision conversion, no training",
        fit_floor_bytes=max(12 * 1024**3, result["output_bytes"] + 8 * 1024**3),
        gpu_inferences=0,
        model_initialisations=0,
    )
    with report_path.open("x") as output:
        json.dump(result, output, indent=2)
        output.write("\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
