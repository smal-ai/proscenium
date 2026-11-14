"""Validate/export final retained viewport observations. No browser/provider startup."""

import argparse
import hashlib
import json
import math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image

REPO = Path(__file__).resolve().parents[3]
DESTINATION = REPO / "docs/evidence/m14-performance"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path):
    return json.loads(path.read_bytes())


def quantile(values, fraction):
    return sorted(values)[math.ceil(len(values) * fraction) - 1]


def stats(values):
    assert values and all(value >= 0 for value in values)
    return {
        "count": len(values),
        "p50": quantile(values, 0.5),
        "p95": quantile(values, 0.95),
        "max": max(values),
    }


def absent(original):
    try:
        fields = Path(f"/proc/{original['pid']}/stat").read_text().rpartition(")")[2].split()
        observed = {"pid": original["pid"], "state": fields[0], "start_ticks": fields[19]}
    except FileNotFoundError:
        observed = None
    gone = (
        observed is None
        or observed["start_ticks"] != original["start_ticks"]
        or observed["state"] in {"Z", "X"}
    )
    assert gone, "Exact owned process is still live"
    return {"original": original, "observed": observed, "original_process_absent": gone}


def main(root: Path):
    assert root.name == "m14-performance-20261114-b"
    raw = load(root / "result.json")
    configured = load(root / "configured-allocation.json")
    reservation = load(root / "measurement-reservation.json")
    assert raw["status"] == "two_retained_cases_measured"
    assert raw["elapsed_seconds"] <= 240 and not raw.get("guard_stop")
    assert configured["limits"] == reservation["limits"] == raw["limits"]
    assert configured["configured_before_browser_launch"]
    assert raw["limits"] == {
        "seconds": 240,
        "min_host_available_bytes": 4 * 1024**3,
        "max_browser_summed_rss_bytes": 4 * 1024**3,
    }
    assert reservation["limit_profiles"] == 1 and reservation["limit_cases"] == 2
    assert raw["cleanup"]["owned_live_after"] == [] and raw["cleanup"]["server_closed"]
    assert raw["cleanup"]["root_after"] is None
    assert raw["observer_failures"] == [] and raw["console_errors"] == []
    assert raw["viewport"] == {"width": 1920, "height": 1080, "dpr": 1}
    samples = raw["resources"]
    assert min(sample["host_available_bytes"] for sample in samples) >= 4 * 1024**3
    assert max(sample["browser_summed_rss_bytes"] for sample in samples) <= 4 * 1024**3
    for file in raw["source_rehash_after"]:
        assert file["unchanged"] and sha(Path(file["path"])) == file["sha256"]
    cases = {}
    targets = {}
    assert [case["name"] for case in raw["cases"]] == ["small", "heavy"]
    for case in raw["cases"]:
        name = case["name"]
        minimum_count = 120 if name == "small" else 60
        rows = case["trace"]
        assert case["status"] == "observed_requested_sample_count" and len(rows) >= minimum_count
        assert all(row["draw_calls"] > 0 and row["end_ms"] >= row["start_ms"] for row in rows)
        intervals = [b["start_ms"] - a["start_ms"] for a, b in zip(rows, rows[1:])]
        callbacks = [row["end_ms"] - row["start_ms"] for row in rows]
        for key, values in (("interval_ms", intervals), ("callback_wall_ms", callbacks)):
            measured = stats(values)
            for field in ("count", "p50", "p95", "max"):
                assert math.isclose(case["cadence"][key][field], measured[field], abs_tol=1e-8)
        assert case["cadence"]["callback_count"] == len(rows)
        assert case["cadence"]["draw_calls"] == sum(row["draw_calls"] for row in rows)
        assert case["mouse_moves"] > 0
        assert case["observer_totals"]["camera_changes"] > case["camera_changes_before_sampling"]
        before, after = case["before_orbit"], case["after_orbit"]
        assert before["sha256"] != after["sha256"]
        assert before["camera"]["camera_to_world"] != after["camera"]["camera_to_world"]
        assert case["draws_before_orbit"] > 0
        for label, capture in (("before", before), ("after", after)):
            image = root / f"{name}-{label}.png"
            assert sha(image) == capture["sha256"]
            assert capture["different_from_corner"] > 100
            with Image.open(image) as pixels:
                pixels.load()
                assert pixels.size == (1600, 900)
        with Image.open(root / f"{name}-fullhd.png") as screenshot:
            assert screenshot.size == (1920, 1080)
        assert "SwiftShader" in case["graphics"]["renderer"]
        resource = case["loader"]["resource"]
        assert (
            resource["encodedBodySize"] == resource["decodedBodySize"] == case["workload"]["bytes"]
        )
        served = [entry for entry in raw["http"] if entry["url"] == f"/fixtures/{name}.glb"]
        assert len(served) == 1 and served[0]["body_bytes"] == case["workload"]["bytes"]
        own_samples = [
            sample
            for sample in samples
            if case["case_start_elapsed_ms"] <= sample["elapsed_ms"] <= case["case_end_elapsed_ms"]
        ]
        assert own_samples
        peak_rss = max(sample["browser_summed_rss_bytes"] for sample in own_samples)
        duration_ms = rows[-1]["start_ms"] - rows[0]["start_ms"]
        cases[name] = {
            "workload": case["workload"],
            "loader": case["loader"],
            "http_server": served[0],
            "cadence": case["cadence"],
            "first_callback": rows[0],
            "last_callback": rows[-1],
            "callback_span_ms": duration_ms,
            "observed_draw_callbacks_per_second": (len(rows) - 1) * 1000 / duration_ms,
            "steady_started_at": case["steady_started_at"],
            "steady_finished_at": case["steady_finished_at"],
            "load_orbit_capture_validation_before_sampling_ms": case["steady_start_elapsed_ms"]
            - case["case_start_elapsed_ms"],
            "visibility": {
                "before": before,
                "after": after,
                "actual_forwarded_draws_before_orbit": case["draws_before_orbit"],
                "mouse_moves_during_sampling": case["mouse_moves"],
                "camera_changes_during_sampling": case["observer_totals"]["camera_changes"]
                - case["camera_changes_before_sampling"],
            },
            "graphics": case["graphics"],
            "resources": {
                "sample_count": len(own_samples),
                "peak_browser_summed_rss_bytes": peak_rss,
                "minimum_host_available_bytes": min(
                    sample["host_available_bytes"] for sample in own_samples
                ),
            },
        }
        targets[name] = {
            "loader_ready_ms": math.ceil(case["loader"]["loader_ready_ms"] * 1.5 + 100),
            "interval_p50_ms": math.ceil(case["cadence"]["interval_ms"]["p50"] * 1.25 + 2),
            "interval_p95_ms": math.ceil(case["cadence"]["interval_ms"]["p95"] * 1.25 + 2),
            "interval_max_ms": math.ceil(case["cadence"]["interval_ms"]["max"] * 1.5 + 5),
            "sampled_browser_summed_rss_bytes": math.ceil(peak_rss * 1.25 / 1024**2) * 1024**2,
            "minimum_draw_callbacks": minimum_count,
        }
    old_files = ["measurement.json", "summary.json", "historical.json", "small-before.png"]
    old_hashes = {name: sha(DESTINATION / name) for name in old_files}
    historical = load(DESTINATION / "historical.json")
    m07_root = REPO / "docs/evidence/m07-fp16/execution"
    latencies = load(m07_root / "latencies.json")
    resources = load(m07_root / "resource-summary.json")
    identities = load(m07_root / "runtime-identities.json")
    assert resources["host_before_start"]["mem_available_bytes"] == 36721819648
    assert resources["minimum_mem_available_bytes"] == 29554343936
    assert resources["maximum_owned_comfy_rss_bytes"] == 2216775680
    assert latencies["m07-fp16-initial"]["comfy_execution_seconds"] == 4.632
    assert latencies["m07-fp16-correction"]["comfy_execution_seconds"] == 1.346
    historical["m07"] = {
        "label": "Read-only join of actual accepted FP16 execution, not another provider measurement",
        "integrated_revision": "dd3abd2",
        "runtime_identities": identities,
        "profile": "comfy-sd-turbo-img2img-fp16-1",
        "dimensions": [512, 512],
        "weights": "float16 model and CLIP; bfloat16 VAE, as observed in the original runtime",
        "latencies": latencies,
        "resource_summary": resources,
        "quality": {
            "requested_cobalt_blue_correction": "FAIL",
            "observation": "Both retained images remain red. Technical completion/latency is not semantic quality acceptance.",
        },
        "limits": "Comfy execution, POST-to-product-job success and owner-Queue-to-native-work completion are separate intervals, not GPU kernel timings. Device-wide VRAM includes protected independent residents, not per-process allocation. RSS is sampled, not an allocator peak.",
    }
    for name in ("latencies.json", "resource-summary.json", "runtime-identities.json", "README.md"):
        historical["sources_sha256"][str((m07_root / name).relative_to(REPO))] = sha(
            m07_root / name
        )
    cleanup = {
        "observed_at": datetime.now(ZoneInfo("Africa/Johannesburg")).isoformat(),
        "browser": absent(raw["browser_root"]),
        "server_and_harness": absent(raw["server"]["identity"]),
        "recorded_cleanup": raw["cleanup"],
    }
    summary = {
        "status": "complete_bounded_observations_heavy_software_orbit_slow_no_m14_claim",
        "tested_commit": raw["implementation_commit"],
        "product_base": raw["prepared"]["base_commit"],
        "retained_root": str(root),
        "component_scope": raw["prepared"]["component_scope"],
        "allocation": {
            "browser_profiles": 1,
            "browser_launches": 1,
            "fixture_servers": 1,
            "proxy_cases": 2,
            "elapsed_seconds": raw["elapsed_seconds"],
            "sdk_sends_or_native_authoring_or_provider_or_encode_work": 0,
        },
        "configured_allocation": configured,
        "reservation": reservation,
        "identities": raw["identities"],
        "viewport": raw["viewport"],
        "cases": cases,
        "observer_failures": raw["observer_failures"],
        "resources": {
            "sample_count": len(samples),
            "peak_browser_summed_rss_bytes": max(
                sample["browser_summed_rss_bytes"] for sample in samples
            ),
            "minimum_host_available_bytes": min(
                sample["host_available_bytes"] for sample in samples
            ),
            "peak_harness_rss_bytes": max(sample["harness_rss_bytes"] for sample in samples),
            "maximum_sample_gap_ms": max(
                b["elapsed_ms"] - a["elapsed_ms"] for a, b in zip(samples, samples[1:])
            ),
            "host_load_average_first": samples[0]["load_average"],
            "host_load_average_last": samples[-1]["load_average"],
            "limits": "Sampled sum of attributed Chromium-process RSS, not PSS or allocator usage. Shared pages count repeatedly. Whole-host available RAM/load includes independent activity. Intervening peaks may be missed.",
        },
        "prospective_regression_targets": {
            "qualification": "Proposed workload-specific future regression thresholds, not a universal SLA, repeated-run confidence bound, executed future gate or performance acceptance",
            "profile": "Same host, Chromium159/SwiftShader, DPR1, 1920x1080 viewport, maintained ProxyView at 1600x900, small then heavy once, no HTTP cache; JavaScript/GL/shader/small-source cache can be warm for heavy",
            "formulas": {
                "loader_ready": "ceil(observed_ms * 1.5 + 100)",
                "interval_p50_p95": "ceil(observed_ms * 1.25 + 2)",
                "interval_max": "ceil(observed_ms * 1.5 + 5)",
                "rss": "ceil(observed per-case sampled summed RSS * 1.25 / MiB) * MiB",
            },
            "cases": targets,
            "heavy_warning": "Regression limits derived from the poor heavy baseline do not make it interactive. The actual median draw-bearing JavaScript callback interval is 391 ms. Proxy optimisation remains unperformed and outside this measurement allocation.",
            "safety_guards_separate_from_targets": raw["limits"],
        },
        "measurement_limits": [
            "Actual forwarded rAF callbacks that issue WebGL draw calls. No GPU fence, gl.finish, timer-query completion or compositor presentation was measured.",
            "Short JavaScript callback wall duration does not measure asynchronous software rendering cost. Heavy draw-loop intervals are slow even though callback bodies and loader readiness are short.",
            "Heavy loader readiness is not first visible-frame latency. The separately recorded pre-sampling validation interval includes actual framebuffer capture, pointer orbit and screenshot work, not an isolated GPU measurement.",
            "Controlled compiled maintained-component fixture, not whole-studio shell/backend/native user-journey acceptance.",
            "One ordered observation per asset, with 123/63 actual draw callbacks. No repeated-run variance, cold-heavy run, hardware-GPU benchmark or browser matrix.",
            "Loopback ResourceTiming and server response completion include scheduling; transferSize is browser accounting rather than packet capture.",
            "The four-GiB ceiling belongs only to the new -b allocation. The original -a two-GiB guard and palette failure remain unchanged.",
            "No M14/release or universal performance-compliance claim. Coordinator assesses NFR04 coverage and the visible heavy-workload weakness.",
        ],
        "source_preservation": raw["source_rehash_after"],
        "cleanup": cleanup,
        "process_identity_records_sha256": {
            name: hashlib.sha256(
                json.dumps(raw[name], sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            for name in ("browser_root", "server", "processes", "cleanup")
        },
        "original_a_evidence_sha256": old_hashes,
        "source_test_hashes": raw["prepared"]["sources"],
        "compiled_build_hashes": raw["prepared"]["build_files"],
        "retained_sha256": {
            name: sha(root / name)
            for name in (
                "result.json",
                "measurement-reservation.json",
                "configured-allocation.json",
                "prepared.json",
            )
        },
    }
    (DESTINATION / "final-measurement.json").write_bytes((root / "result.json").read_bytes())
    (DESTINATION / "final-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (DESTINATION / "final-historical.json").write_text(json.dumps(historical, indent=2) + "\n")
    images = {}
    for name in cases:
        for view in ("before", "after", "fullhd"):
            source = root / f"{name}-{view}.png"
            target = DESTINATION / f"final-{name}-{view}.png"
            target.write_bytes(source.read_bytes())
            images[target.name] = sha(source)
    (DESTINATION / "final-images.json").write_text(json.dumps(images, indent=2) + "\n")
    assert old_hashes == {name: sha(DESTINATION / name) for name in old_files}
    print(
        json.dumps(
            {
                "status": summary["status"],
                "allocation": summary["allocation"],
                "resources": summary["resources"],
                "prospective_targets": targets,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    main(parser.parse_args().root.resolve(strict=True))
