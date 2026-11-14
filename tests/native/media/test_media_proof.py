"""Offline generator/oracle checks, without launching native applications."""

import importlib.util
from fractions import Fraction
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[3] / "tools/proofs/media/proof.py"
spec = importlib.util.spec_from_file_location("e03_media_proof", MODULE)
proof = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proof)


@pytest.mark.parametrize("ordinal", range(1, 13))
def test_numbered_frame_round_trip(ordinal):
    image = proof.numbered_frame(ordinal)
    assert image.size == (640, 360) and image.mode == "RGB"
    assert proof.ordinal_from_rgb(image.tobytes()) == ordinal
    assert image.tobytes() == proof.numbered_frame(ordinal).tobytes()


@pytest.mark.parametrize("fps", [Fraction(24, 1), Fraction(24000, 1001)])
def test_exact_segment_centres(fps):
    for start in (1, 5, 9):
        for frame in range(start, start + 4):
            centre = Fraction(2 * (frame - start) + 1, 2) / fps
            assert centre * fps - Fraction(1, 2) + start == frame


def test_blank_is_not_a_valid_numbered_frame():
    assert proof.ordinal_from_rgb(bytes(640 * 360 * 3)) == 0


def test_ambiguous_marker_rejected():
    with pytest.raises(AssertionError, match="Ambiguous"):
        proof.ordinal_from_rgb(bytes([127]) * (640 * 360 * 3))
