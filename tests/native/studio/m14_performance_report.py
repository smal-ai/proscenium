"""Export existing browser measurements and historical rows. No native startup."""

import argparse
import hashlib
import json
import math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[3]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(relative):
    return json.loads((REPO / relative).read_bytes())


def observe_identity(original):
    try:
        fields = Path(f"/proc/{original['pid']}/stat").read_text().rpartition(")")[2].split()
        actual = {"pid": original["pid"], "state": fields[0], "start_ticks": fields[19]}
    except FileNotFoundError:
        actual = None
    absent = (
        actual is None
        or actual["start_ticks"] != original["start_ticks"]
        or actual["state"] in {"Z", "X"}
    )
    assert absent, "Previously owned measurement process still live"
    return {"original": original, "observed": actual, "original_process_absent": absent}


def main(root: Path):
    raw = json.loads((root / "result.json").read_bytes())
    assert raw["elapsed_seconds"] <= 240
    assert raw["cleanup"]["owned_live_after"] == [] and raw["cleanup"]["server_closed"]
    assert raw["cleanup"]["root_after"] is None
    assert len(raw["prepared"]["cases"]) == 2
    post_process_cleanup = {
        "observed_at": datetime.now(ZoneInfo("Africa/Johannesburg")).isoformat(),
        "browser": observe_identity(raw["browser_root"]),
        "server_and_harness": observe_identity(raw["server"]["identity"]),
    }
    assert all(record["unchanged"] for record in raw["source_rehash_after"])
    for record in raw["source_rehash_after"]:
        assert sha(Path(record["path"])) == record["sha256"]
    samples = raw["resources"]
    assert samples
    assert min(sample["host_available_bytes"] for sample in samples) >= 4 * 1024**3
    assert max(sample["browser_summed_rss_bytes"] for sample in samples) <= 2 * 1024**3
    small = next(record for record in raw["cases"] if record["name"] == "small")
    assert small["loader"]["resource"]["encodedBodySize"] == 1788
    actual_image = root / "small-before.png"
    assert sha(actual_image) == small["before_orbit"]["sha256"]
    summary = {
        "status": "partial_harness_visibility_false_positive_nfr04_2_open",
        "tested_commit": raw["implementation_commit"],
        "retained_root": str(root),
        "allocation": {
            "profiles": 1,
            "browser_launches": 1,
            "owned_fixture_servers": 1,
            "proxy_cases_loaded": 1,
            "proxy_cases_prepared": 2,
            "sdk_sends": 0,
            "native_authoring_or_render_or_inference": 0,
        },
        "elapsed_seconds": raw["elapsed_seconds"],
        "small": {
            "workload": small["workload"],
            "http": small["loader"]["resource"],
            "loader_ready_ms": small["loader"]["loader_ready_ms"],
            "server_http": next(row for row in raw["http"] if row["url"] == "/fixtures/small.glb"),
            "canvas_capture": small["before_orbit"],
            "graphics": small["graphics"],
            "orbit_verified": False,
            "render_loop_callback_count": len(small["trace"]),
            "cadence_ms": {"p50": None, "p95": None, "max": None},
        },
        "heavy": {
            "status": "not_loaded",
            "workload": raw["prepared"]["cases"][1]["workload"],
            "loader_ready_ms": None,
            "render_loop_callback_count": 0,
        },
        "resources": {
            "sample_count": len(samples),
            "peak_browser_summed_rss_bytes": max(
                sample["browser_summed_rss_bytes"] for sample in samples
            ),
            "minimum_host_available_bytes": min(
                sample["host_available_bytes"] for sample in samples
            ),
            "load_average_first": samples[0]["load_average"],
            "load_average_last": samples[-1]["load_average"],
            "sampled_not_exact_peak": True,
            "rss_not_pss": "Sum of attributed process RSS counts shared pages more than once. Includes all observed browser-group/descendant processes, not merely the renderer tab.",
        },
        "failure": {
            "message": raw["error"],
            "cause": "Harness required more than sixteen quantised colours. The actual flat-shaded block has ten colours and 15,554 sampled non-background pixels, so the palette heuristic falsely rejected visible geometry.",
            "not_a_product_performance_failure": True,
            "correction": "Pure predicate now requires non-uniform real pixels and a non-background pixel area, not a large palette. Exact recorded input and blank-frame negatives are tested. No browser rerun occurred.",
        },
        "prospective_targets": {
            "scope": "Same retained small GLB, maintained ProxyView fixture, Full-HD/DPR1, 1600x900 canvas, Chromium159/SwiftShader on this host. Single-observation provisional candidate, not qualified by orbit/cadence and not a universal SLA.",
            "small_loader_ready_candidate_ms": math.ceil(
                small["loader"]["loader_ready_ms"] * 1.5 + 100
            ),
            "margin_formula": "ceil(observed loader-ready milliseconds * 1.5 + 100)",
            "cadence": None,
            "heavy": None,
            "missing_reason": "No measured cadence or heavy load exists. Targets remain unset rather than derived from fabricated samples.",
            "resource_guards_not_regression_targets": {
                "host_available_min_bytes": 4 * 1024**3,
                "browser_summed_rss_max_bytes": 2 * 1024**3,
            },
        },
        "cleanup": raw["cleanup"],
        "post_process_cleanup": post_process_cleanup,
        "browser_root": raw["browser_root"],
        "server": raw["server"],
        "source_preservation": raw["source_rehash_after"],
        "evidence_sha256": {
            "raw_result": sha(root / "result.json"),
            "canvas_png": sha(actual_image),
        },
        "limits": [
            "No orbit verification or steady cadence sample was completed.",
            "Heavy GLB/mapping were copied and hash-checked only, not loaded by the browser.",
            "Loader readiness is model layout/CaptureBinding readiness, not a GPU fence or compositor presentation.",
            "Loopback resource timing includes browser scheduling, not pure network bandwidth.",
            "Original code/test/build hashes are retained in measurement.json. The corrected palette predicate was not browser-executed.",
            "No replacement browser/profile/server, production change or service lifecycle action is authorised implicitly.",
            "NFR04.2 viewport cadence, complete performance acceptance and M14/release remain open.",
        ],
        "coordinator_question": "Authorise one replacement browser measurement after the proven palette-check defect, with the same two retained cases and unchanged time/RSS/headroom guards? No replacement was launched.",
    }
    w03 = load("docs/evidence/w03/native.json")
    m08 = load("docs/evidence/m08/final-run.json")
    e04 = load("docs/evidence/w09/live-b/native-report.json")
    m09 = load("docs/evidence/m09/vlm-responses-redacted.json")
    positive = load("docs/evidence/m09-positive/vlm-observations.json")
    source_paths = [
        "docs/evidence/w03/native.json",
        "docs/evidence/m08/final-run.json",
        "docs/implementation/m08.md",
        "docs/evidence/w09/live-b/native-report.json",
        "docs/evidence/w09/live-b/README.md",
        "docs/evidence/m09/vlm-responses-redacted.json",
        "docs/evidence/m09/result.json",
        "docs/evidence/m09-positive/vlm-observations.json",
        "docs/evidence/m09-positive/README.md",
    ]
    history = {
        "label": "Read-only historical measurements. No native/model/renderer/provider run repeated on the performance-task revision.",
        "sources_sha256": {name: sha(REPO / name) for name in source_paths},
        "w03": {
            "tested_base": w03["base_commit"],
            "implementation_commits": w03["implementation_commits"],
            "runtime": w03["runtime"],
            "performance_seconds": w03["performance_seconds"],
            "renderer_reported_peak_memory_M": w03["renderer_reported_peak_memory_M"],
            "os_telemetry_limit": w03["os_rss_cpu_gpu_telemetry"],
            "historical_integration_gap": w03["integration_gap"],
        },
        "m08": {
            "tested_parent_dependency": "4607c17 including a46c9ba; driver/evidence 2d7a32ab93875c8ea507938b045c3267c20ddb56, as recorded in docs/implementation/m08.md",
            "runtime": m08["runtime"],
            "performance": {
                key: value
                for key, value in m08["progressive"].items()
                if key
                not in {
                    "job",
                    "annotation",
                    "rvfc_order_with_intentional_pause_replay",
                    "increasing_callback_intervals_ms",
                }
            },
            "os_telemetry_limit": m08["progressive"]["os_rss_and_cpu_percent"],
        },
        "e04_live_b": {
            "tested_code_revision": e04["code_revision"],
            "runtime": "ComfyUI0.37.4 revision8ff6dc384ba5c410266b40e137799e049459d4f2; torch2.13.0+rocm10.0.0; AMD8060S/gfx1151, see original README",
            "provider_dispatch_to_registered_result_seconds": (
                e04["inference_dispatch_ended_monotonic_ns"]
                - e04["inference_dispatch_started_monotonic_ns"]
            )
            / 1e9,
            "comfy_reported_prompt_execution_seconds": 70.78,
            "sampled_resources": e04["sampled_resources"],
            "limits": "Provider timing includes upload/poll/registration, not GPU kernel time. Device-wide VRAM includes independent services. Blender RSS and exact allocator peak were not measured.",
        },
        "m09_initial": {
            "tested_base": "b1fa32c8a73d5aca8243fa82e65837a8acaba75c",
            "responses": [
                {
                    "seconds": row["seconds"],
                    "model": row["model"],
                    "usage": row["usage"],
                    "finish_reason": row["choices"][0]["finish_reason"],
                }
                for row in m09
                if row["phase"] == "response"
            ],
            "quality_limit": "First response exhausted 512 tokens with empty content; second mixed visual/measured evidence. Both remain failed-quality results, not successful findings.",
            "os_rss_limit": "No per-response process RSS or GPU allocator peak recorded.",
        },
        "m09_positive": {
            "tested_base": "ad47260a922612393896c21ff7186d643037c524",
            "driver_commit": "b8a68c043c361c53b12ee2ca2328daa944465cdf",
            "model": "google/gemma-4-e4b",
            "configuration": "65536 context; batch4096/ubatch2048; K/Vf16; flash auto; TTLdisabled; reasoning_effort none; max_tokens512",
            "responses": [
                {
                    "seconds": row["seconds"],
                    "usage": row["body"]["usage"],
                    "finish_reason": row["body"]["choices"][0]["finish_reason"],
                }
                for row in positive
                if row["phase"] == "response"
            ],
            "os_rss_limit": "Pre-POST host MemAvailable recorded, not per-response process RSS or GPU allocator peak.",
        },
        "m07": "Intentionally not joined here. Coordinator will add the returning inference measurements.",
    }
    destination = REPO / "docs/evidence/m14-performance"
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "measurement.json").write_bytes((root / "result.json").read_bytes())
    for name, value in (
        ("summary.json", summary),
        ("historical.json", history),
    ):
        (destination / name).write_text(json.dumps(value, indent=2) + "\n")
    (destination / "small-before.png").write_bytes(actual_image.read_bytes())
    print(
        json.dumps(
            {
                key: summary[key]
                for key in (
                    "status",
                    "elapsed_seconds",
                    "resources",
                    "prospective_targets",
                    "coordinator_question",
                )
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    main(parser.parse_args().root.resolve(strict=True))
