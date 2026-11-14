"""Read retained actual GPU/native/UI receipts. No new inference or service work."""

import hashlib
import json
from pathlib import Path

from PIL import Image

EVIDENCE = Path(__file__).parents[2] / "docs/evidence/m07-fp16/execution"


def load(name):
    return json.loads((EVIDENCE / name).read_bytes())


def test_two_actual_native_admissions_and_same_frozen_frame_with_honest_new_tasks():
    final = load("post-cleanup.json")
    assert len(final["jobs"]) == len(final["job_inputs"]) == len(final["reservations"]) == 2
    assert all(j["state"] == "succeeded" and j["attempt_number"] == 1 for j in final["jobs"])
    assert len({j["target"]["target_id"] for j in final["jobs"]}) == 1
    assert len({r["task_id"] for r in final["reservations"]}) == 2
    assert all(r["state"] == "consumed" for r in final["reservations"])
    assert len(load("native-delivery.json")) == 2
    tools = [v for v in load("generation-tool-results.json") if v["tool"] == "submit_generation"]
    assert len(tools) == 2
    for row in tools:
        args = row["arguments"]
        job = next(j for j in final["jobs"] if j["job_id"] == args["job_id"])
        work = next(w for w in final["work"] if w["work_id"] == job["work_id"])
        policy = next(
            p for p in final["providers.tasks"] if p["task_id"] == args["generation_task_id"]
        )
        assert policy["limit"] == 3 and policy["targets"] == [job["target"]["target_id"]]
        request = next(
            r
            for r in load("public-collect.json")["state"]["requests"]["items"]
            if r["request_id"] == work["originating_request_id"]
        )
        assert request["generation_task_id"] == args["generation_task_id"]
        assert len(final["copilot.effective"]) == 2
        assert all(p["generation_attempt_limit"] == 3 for p in final["copilot.effective"])
        assert args["generation_task_id"] == work["generation_task_id"]
        assert args["settings"]["target_slot"]["frame_id"] == "frame-mug"
        assert job["target"]["shot_id"] == "shot-mug"


def test_live_one_post_ack_loss_uncertainty_history_match_and_same_attempt_reuse():
    fault = load("fault-known-response.json")
    assert fault["real_ack"]["status"] == 200
    prompt = fault["real_ack"]["body"]["prompt_id"]
    assert fault["real_history"][prompt]["status"]["completed"]
    assert fault["state_before_fault"]["jobs"][0]["state"] == "starting"
    assert fault["state_before_fault"]["jobs"][0]["external_job_id"] is None
    assert fault["state_before_fault"]["reservations"][0]["state"] == "reserved"
    reads = load("provider-reads-during-uncertainty.json")
    assert any(r["path"] == "/history" and prompt in r["response"] for r in reads)
    assert all(r["state"]["reservations"][0]["state"] == "reserved" for r in reads)
    request = load("prompt-request-m07-fp16-initial.json")
    final = next(j for j in load("post-cleanup.json")["jobs"] if j["job_id"] == "m07-fp16-initial")
    assert final["external_job_id"] == prompt
    assert (
        final["attempt_id"] == request["job"]["attempt_id"]
        and final["input_digest"] == request["job"]["input_digest"]
    )
    assert len(list(EVIDENCE.glob("prompt-request-*.json"))) == 2
    assert len(load("provider-terminal-history.json")) == 2
    transitions = [
        e["payload"]
        for e in load("public-collect.json")["state"]["events"]
        if e["event_type"] == "job.state_changed" and e["payload"]["job_id"] == "m07-fp16-initial"
    ]
    assert [j["state"] for j in transitions] == [
        "planned",
        "starting",
        "outcome_uncertain",
        "running",
        "succeeded",
    ]
    assert {j["attempt_id"] for j in transitions} == {final["attempt_id"]}
    assert {j["input_digest"] for j in transitions} == {final["input_digest"]}
    history = load("provider-terminal-history.json")[prompt]
    assert history["prompt"][3]["client_id"] == request["body"]["client_id"]


def test_actual_img2img_graph_prompt_model_hashes_and_correction_reference_chain():
    final = load("post-cleanup.json")
    first = load("public-collect.json")["state"]["artifacts"]["m07-fp16-initial-artifact-0"]
    for inputs in final["job_inputs"]:
        job = inputs["request"]["job_id"]
        provenance = load(job + "-provenance.json")
        g = provenance["graph"]
        assert inputs["plugin"]["revision"] == "comfy-sd-turbo-img2img-fp16-1"
        assert inputs["plugin"]["expected_model_hashes"] == {
            "sd_turbo-proscenium-fp16-1.safetensors": "c1a0a0af943c0c197215fb7cd5bb8b72e342b98461e1c3719d8ba28300ba015e"
        }
        assert inputs["provider"]["endpoint"] == "http://127.0.0.1:18589"
        assert g["encode"]["inputs"]["pixels"] == ["reference", 0]
        assert g["sample"]["inputs"]["latent_image"] == ["encode", 0]
        assert g["guider"]["inputs"]["positive"] == ["text", 0]
        assert g["sigmas"]["inputs"]["steps"] == 4 and g["sigmas"]["inputs"]["denoise"] == 0.5
        assert g["text"]["inputs"]["text"] == inputs["prompt"]
        assert not g["reference"]["inputs"]["image"].startswith("artifact:")
        assert provenance["derived_images"]
        if job == "m07-fp16-correction":
            assert inputs["references"]["m07-fp16-initial-artifact-0"]["sha256"] == first["sha256"]


def test_explicit_ui_choices_preserve_prior_choice_before_supersession_and_unrelated_frame():
    chosen = load("public-select-initial.json")
    before = load("public-select-correction.json")["before"]
    final = load("public-collect.json")["state"]

    def find(state, frame):
        return next(s for s in state["selections"]["items"] if s["slot"]["frame_id"] == frame)

    assert (
        find(chosen["before"], "frame-unrelated")["choice"]["artifact_id"]
        == "m07-unrelated-reference"
    )
    assert not any(
        s["slot"]["frame_id"] == "frame-mug" for s in chosen["before"]["selections"]["items"]
    )
    assert (
        find(chosen["state"], "frame-mug")["choice"]["artifact_id"] == "m07-fp16-initial-artifact-0"
    )
    assert find(before, "frame-mug")["choice"]["artifact_id"] == "m07-fp16-initial-artifact-0"
    assert find(final, "frame-mug")["choice"]["artifact_id"] == "m07-fp16-correction-artifact-0"
    assert find(final, "frame-mug")["state_revision"] == 2
    assert find(final, "frame-unrelated") == find(chosen["before"], "frame-unrelated")


def test_actual_pngs_hash_reuse_no_extra_post_and_full_hd():
    for name, metadata in load("manifest.json").items():
        raw = (EVIDENCE / name).read_bytes()
        assert len(raw) == metadata["bytes"]
        assert hashlib.sha256(raw).hexdigest() == metadata["sha256"]
    public = load("public-collect.json")
    reuse = load("public-reuse.json")
    for job in ["m07-fp16-initial", "m07-fp16-correction"]:
        metadata = public["state"]["artifacts"][job + "-artifact-0"]
        raw = (EVIDENCE / (job + ".png")).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == metadata["sha256"]
        with Image.open(EVIDENCE / (job + ".png")) as image:
            assert image.format == "PNG" and image.size == (512, 512)
        assert (
            next(row for row in reuse["hashes"] if row["id"] == job + "-artifact-0")["sha256"]
            == metadata["sha256"]
        )
    assert reuse["before"]["jobs"] == reuse["state"]["jobs"]
    for name in [
        "activate",
        "initial",
        "select-initial",
        "correction",
        "select-correction",
        "reuse",
        "collect",
    ]:
        assert load("public-" + name + ".json")["viewport"] == {
            "width": 1920,
            "height": 1080,
            "dpr": 1,
        }
        with Image.open(EVIDENCE / ("browser-" + name + ".png")) as image:
            assert image.size == (1920, 1080)


def test_measured_resources_and_exact_owned_cleanup_with_limit_disclosures():
    final = load("post-cleanup.json")
    resources = load("resource-summary.json")
    assert final["host_exited"] and final["sdk_exited"] and final["comfy_state"] in {"Z", "exited"}
    assert final["remaining_claims"] == [] and final["children_count"] == 0
    assert final["original_files_unchanged"] and final["source_unchanged"]
    assert resources["minimum_mem_available_bytes"] >= 5 * 1024**3
    assert resources["maximum_gpu_busy_percent"] > 0
    assert resources["host_before_start"]["mem_available_bytes"] >= 12 * 1024**3
    assert resources["host_after_startup"]["mem_available_bytes"] >= 12 * 1024**3
    assert "NOT achieved" in load("verification.json")["quality_limit"]
    assert load("verification.json")["native_rpc_ack_loss"] == "NOT_TESTED"
    for row in load("latencies.json").values():
        assert 0 < row["owner_queue_accepted_to_native_work_completed_seconds"] <= 180
    for phase in ["initial", "correction"]:
        request = load(f"prompt-request-m07-fp16-{phase}.json")
        assert request["pre_post_resources"]["mem_available_bytes"] >= 12 * 1024**3
