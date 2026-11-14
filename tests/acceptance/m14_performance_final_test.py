"""Final retained viewport metrics. No browser, native process or provider startup."""

import hashlib
import json
import math
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "docs/evidence/m14-performance"


def load(name):
    return json.loads((EVIDENCE / name).read_bytes())


def test_separate_final_allocation_keeps_original_failure_and_old_guard():
    final = load("final-summary.json")
    assert final["status"] == "complete_bounded_observations_heavy_software_orbit_slow_no_m14_claim"
    assert final["allocation"]["browser_launches"] == final["allocation"]["browser_profiles"] == 1
    assert final["allocation"]["fixture_servers"] == 1 and final["allocation"]["proxy_cases"] == 2
    assert final["allocation"]["elapsed_seconds"] <= 240
    assert final["observer_failures"] == []
    assert final["configured_allocation"]["limits"]["max_browser_summed_rss_bytes"] == 4 * 1024**3
    assert load("measurement.json")["limits"]["max_browser_summed_rss_bytes"] == 2 * 1024**3
    assert load("summary.json")["small"]["render_loop_callback_count"] == 0
    for name, expected in final["original_a_evidence_sha256"].items():
        assert hashlib.sha256((EVIDENCE / name).read_bytes()).hexdigest() == expected


def test_actual_callback_samples_and_nearest_rank_intervals_not_gpu_completion():
    raw, final = load("final-measurement.json"), load("final-summary.json")
    assert [case["name"] for case in raw["cases"]] == ["small", "heavy"]
    for case in raw["cases"]:
        rows = case["trace"]
        assert len(rows) >= (120 if case["name"] == "small" else 60)
        assert all(row["draw_calls"] > 0 for row in rows)
        intervals = sorted(b["start_ms"] - a["start_ms"] for a, b in zip(rows, rows[1:]))
        measured = final["cases"][case["name"]]["cadence"]
        assert measured["callback_count"] == len(rows)
        assert measured["interval_ms"]["count"] == len(rows) - 1
        assert measured["interval_ms"]["p50"] == intervals[math.ceil(len(intervals) * 0.5) - 1]
        assert measured["interval_ms"]["p95"] == intervals[math.ceil(len(intervals) * 0.95) - 1]
        assert measured["interval_ms"]["max"] == max(intervals)
        assert case["observer_totals"]["camera_changes"] > case["camera_changes_before_sampling"]
    assert final["cases"]["heavy"]["cadence"]["interval_ms"]["p50"] == 391
    assert "No GPU fence" in " ".join(final["measurement_limits"])


def test_actual_framebuffers_and_changed_orbit_before_sampling():
    summary = load("final-summary.json")
    images = load("final-images.json")
    for name, expected in images.items():
        assert hashlib.sha256((EVIDENCE / name).read_bytes()).hexdigest() == expected
        with Image.open(EVIDENCE / name) as pixels:
            pixels.load()
            assert pixels.size == ((1920, 1080) if "fullhd" in name else (1600, 900))
    for case in summary["cases"].values():
        visible = case["visibility"]
        assert visible["before"]["different_from_corner"] > 100
        assert visible["after"]["different_from_corner"] > 100
        assert visible["before"]["sha256"] != visible["after"]["sha256"]
        assert (
            visible["before"]["camera"]["camera_to_world"]
            != visible["after"]["camera"]["camera_to_world"]
        )
        assert visible["actual_forwarded_draws_before_orbit"] > 0


def test_http_bytes_resource_guard_cleanup_and_original_sources():
    final = load("final-summary.json")
    for name, expected_size in (("small", 1788), ("heavy", 90077656)):
        case = final["cases"][name]
        assert case["loader"]["resource"]["encodedBodySize"] == expected_size
        assert case["http_server"]["body_bytes"] == expected_size
    assert final["cases"]["heavy"]["workload"]["triangles"] == 1304618
    assert final["resources"]["peak_browser_summed_rss_bytes"] == 2092216320
    assert final["resources"]["minimum_host_available_bytes"] >= 4 * 1024**3
    assert final["cleanup"]["browser"]["original_process_absent"]
    assert final["cleanup"]["server_and_harness"]["original_process_absent"]
    assert final["cleanup"]["recorded_cleanup"]["owned_live_after"] == []
    assert all(record["unchanged"] for record in final["source_preservation"])
    assert len(final["source_preservation"]) == 4
    assert (
        final["source_preservation"][2]["sha256"]
        == "7631a575fa9dfb3478fbbb5fa26dcff0f6ff4741956856933f23e6c8be0c219e"
    )


def test_future_regression_threshold_formulas_do_not_hide_slow_heavy_baseline():
    summary = load("final-summary.json")
    proposal = summary["prospective_regression_targets"]
    assert "not a universal SLA" in proposal["qualification"]
    assert "poor heavy baseline" in proposal["heavy_warning"]
    for name, case in summary["cases"].items():
        target = proposal["cases"][name]
        assert target["loader_ready_ms"] == math.ceil(case["loader"]["loader_ready_ms"] * 1.5 + 100)
        assert target["interval_p95_ms"] == math.ceil(
            case["cadence"]["interval_ms"]["p95"] * 1.25 + 2
        )
    assert proposal["cases"]["small"]["interval_p95_ms"] == 24
    assert proposal["cases"]["heavy"]["interval_p95_ms"] == 873


def test_m07_historical_fp16_join_preserves_quality_and_vram_limits():
    history = load("final-historical.json")
    m07 = history["m07"]
    assert m07["dimensions"] == [512, 512]
    assert m07["latencies"]["m07-fp16-initial"]["comfy_execution_seconds"] == 4.632
    assert m07["latencies"]["m07-fp16-correction"]["comfy_execution_seconds"] == 1.346
    assert m07["resource_summary"]["host_before_start"]["mem_available_bytes"] == 36721819648
    assert m07["resource_summary"]["minimum_mem_available_bytes"] == 29554343936
    assert m07["resource_summary"]["maximum_owned_comfy_rss_bytes"] == 2216775680
    assert m07["resource_summary"]["all_device_vram_counters_include_protected_other_residents"]
    assert m07["quality"]["requested_cobalt_blue_correction"] == "FAIL"
    assert history["m08"]["os_telemetry_limit"] == "not sampled"
    for name, expected in history["sources_sha256"].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected
