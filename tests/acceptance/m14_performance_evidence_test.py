"""Retained partial measurement integrity. No browser, server or native execution."""

import hashlib
import json
from pathlib import Path

from PIL import Image

EVIDENCE = Path(__file__).resolve().parents[2] / "docs/evidence/m14-performance"


def load(name):
    return json.loads((EVIDENCE / name).read_bytes())


def test_partial_is_not_relabelled_complete_or_fake_cadence():
    result = load("summary.json")
    assert (
        hashlib.sha256((EVIDENCE / "measurement.json").read_bytes()).hexdigest()
        == result["evidence_sha256"]["raw_result"]
    )
    assert result["status"] == "partial_harness_visibility_false_positive_nfr04_2_open"
    assert result["allocation"]["profiles"] == result["allocation"]["browser_launches"] == 1
    assert result["small"]["render_loop_callback_count"] == 0
    assert all(value is None for value in result["small"]["cadence_ms"].values())
    assert result["heavy"]["status"] == "not_loaded"
    assert result["prospective_targets"]["cadence"] is None
    assert result["prospective_targets"]["heavy"] is None
    assert result["elapsed_seconds"] <= 240


def test_measured_small_bytes_latency_and_resource_guards():
    result = load("summary.json")
    assert result["small"]["workload"]["triangles"] == 12
    assert result["small"]["http"]["encodedBodySize"] == 1788
    assert result["small"]["server_http"]["body_bytes"] == 1788
    assert 400 < result["small"]["loader_ready_ms"] < 450
    assert result["prospective_targets"]["small_loader_ready_candidate_ms"] == 727
    assert result["resources"]["minimum_host_available_bytes"] >= 4 * 1024**3
    assert result["resources"]["peak_browser_summed_rss_bytes"] <= 2 * 1024**3


def test_real_flat_shaded_capture_explains_palette_false_positive():
    result = load("summary.json")
    png = EVIDENCE / "small-before.png"
    assert (
        hashlib.sha256(png.read_bytes()).hexdigest() == result["small"]["canvas_capture"]["sha256"]
    )
    with Image.open(png) as image:
        assert image.size == (1600, 900)
        rgb = image.convert("RGB").tobytes()
        pixels = [tuple(rgb[i : i + 3]) for i in range(0, len(rgb), 12)]
    colours = {(r >> 3, g >> 3, b >> 3) for r, g, b in pixels}
    corner = pixels[0]
    different = sum(sum(abs(a - b) for a, b in zip(pixel, corner)) > 30 for pixel in pixels)
    assert len(colours) == 10 and different == 15554
    assert result["failure"]["not_a_product_performance_failure"]


def test_exact_pid_start_cleanup_and_source_identity_are_retained():
    result = load("summary.json")
    assert result["browser_root"]["pid"] == 4168896
    assert result["browser_root"]["start_ticks"] == "47366920"
    assert result["cleanup"]["root_after"] is None
    assert result["cleanup"]["owned_live_after"] == [] and result["cleanup"]["server_closed"]
    assert len(result["source_preservation"]) == 4
    assert all(file["unchanged"] for file in result["source_preservation"])
    heavy = load("measurement.json")["prepared"]["cases"][1]
    assert heavy["workload"]["bytes"] == 90077656 and heavy["workload"]["triangles"] == 1304618
    assert (
        heavy["records"][0]["sha256"]
        == "7631a575fa9dfb3478fbbb5fa26dcff0f6ff4741956856933f23e6c8be0c219e"
    )


def test_historical_rows_keep_their_revisions_quality_and_missing_telemetry():
    history = load("historical.json")
    assert history["w03"]["tested_base"] != load("summary.json")["tested_commit"]
    assert history["m08"]["performance"]["os_rss_and_cpu_percent"] == "not sampled"
    assert history["e04_live_b"]["provider_dispatch_to_registered_result_seconds"] == 72.981861025
    assert history["m09_initial"]["responses"][0]["finish_reason"] == "length"
    assert len(history["m09_positive"]["responses"]) == 2
    assert "Coordinator" in history["m07"]
