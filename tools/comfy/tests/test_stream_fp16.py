"""Synthetic CPU-only safetensors fixtures. No checkpoint/model/inference loading."""

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).parents[1]))
import stream_fp16 as converter  # noqa: E402


class NoopGuard:
    def check(self):
        pass


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture(path, tensors, metadata=None):
    body = bytearray()
    header = {"__metadata__": metadata or {"origin": "synthetic unit fixture", "unicode": "µ"}}
    for name, dtype, shape, raw in tensors:
        header[name] = dict(
            dtype=dtype, shape=shape, data_offsets=[len(body), len(body) + len(raw)]
        )
        body.extend(raw)
    raw_header = json.dumps(header, ensure_ascii=False).encode()
    raw_header += b" " * (-len(raw_header) % 8)
    path.write_bytes(len(raw_header).to_bytes(8, "little") + raw_header + body)


class ConversionTests(unittest.TestCase):
    def test_streams_deterministically_and_preserves_metadata_names_shapes_other_types(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.safetensors"
            values = np.concatenate(
                (
                    np.array([0, -0.0, 1, -1, 65504, 2**-24, 2**-25], dtype="<f4"),
                    np.linspace(-7, 7, 1027, dtype="<f4"),
                )
            )
            integer = np.array([1, 2**50, -9], dtype="<i8").tobytes()
            half = np.array([-0.25, 2], dtype="<f2").tobytes()
            fixture(
                source,
                [
                    ("integer", "I64", [3], integer),
                    ("float", "F32", [len(values)], values.tobytes()),
                    ("existing-half", "F16", [2], half),
                    ("empty", "U8", [0], b""),
                ],
            )
            digest = sha(source)
            source.chmod(0o400)
            with patch.object(converter, "CHUNK_BYTES", 64):
                first = converter.convert_file(
                    source, root / "first.safetensors", digest, NoopGuard()
                )
                second = converter.convert_file(
                    source, root / "second.safetensors", digest, NoopGuard()
                )
            self.assertEqual(first["output_sha256"], second["output_sha256"])
            self.assertEqual(sha(source), digest)
            self.assertGreater(first["converted_chunks"], 10)
            self.assertEqual(first["converted_elements"], len(values))
            self.assertEqual(first["preserved_other_payload_bytes"], len(integer) + len(half))
            header, start, _ = converter.layout(root / "first.safetensors")
            old_header, _, _ = converter.layout(source)
            self.assertEqual(header["__metadata__"], old_header["__metadata__"])
            self.assertEqual(header["float"]["dtype"], "F16")
            with (root / "first.safetensors").open("rb") as stream:
                for name, expected in (("integer", integer), ("existing-half", half)):
                    stream.seek(start + header[name]["data_offsets"][0])
                    self.assertEqual(stream.read(len(expected)), expected)
            self.assertEqual((root / "first.safetensors").stat().st_mode & 0o222, 0)
            with self.assertRaises(FileExistsError):
                converter.convert_file(source, root / "first.safetensors", digest, NoopGuard())

    def test_rejects_nonfinite_and_overflow_without_publishing_or_clamping(self):
        for value in (float("nan"), float("inf"), -float("inf"), 70000):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source, output = root / "source.safetensors", root / "output.safetensors"
                fixture(source, [("bad", "F32", [1], np.array([value], dtype="<f4").tobytes())])
                digest = sha(source)
                with self.assertRaises(ValueError):
                    converter.convert_file(source, output, digest, NoopGuard())
                self.assertFalse(output.exists())
                self.assertEqual(sha(source), digest)

    def test_rejects_gaps_overlap_bad_shape_and_uncovered_payload(self):
        for mutation in ("gap", "overlap", "shape", "tail"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "bad.safetensors"
                fixture(path, [("a", "F32", [2], b"\x00" * 8), ("b", "I64", [1], b"\x00" * 8)])
                header, start, _ = converter.layout(path)
                payload = path.read_bytes()[start:]
                if mutation == "gap":
                    header["b"]["data_offsets"] = [12, 20]
                if mutation == "overlap":
                    header["b"]["data_offsets"] = [4, 12]
                if mutation == "shape":
                    header["a"]["shape"] = [3]
                if mutation == "tail":
                    payload += b"x"
                raw = json.dumps(header).encode()
                raw += b" " * (-len(raw) % 8)
                path.write_bytes(len(raw).to_bytes(8, "little") + raw + payload)
                with self.assertRaises(ValueError):
                    converter.layout(path)

    def test_rejects_wrong_hash_duplicate_keys_and_precision_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.safetensors"
            fixture(source, [("a", "F32", [1], b"\x00" * 4)])
            with self.assertRaises(ValueError):
                converter.convert_file(source, root / "output.safetensors", "0" * 64, NoopGuard())
            with self.assertRaises(ValueError):
                json.loads('{"a":1,"a":2}', object_pairs_hook=converter.unique_object)
            with self.assertRaises(ValueError):
                converter.precision(np.array([1], dtype="<f4"), np.array([2], dtype="<f2"))


if __name__ == "__main__":
    unittest.main()
