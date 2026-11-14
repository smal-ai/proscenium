"""Offline retained-proof checks. Passing integrity checks do not claim adoption."""

import hashlib
import json
from pathlib import Path

from proscenium.contracts import validate

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "docs/evidence/m05-adoption"


def read(name):
    return json.loads((EVIDENCE / name).read_text())


def test_one_actual_send_same_native_session_and_no_new_execution_jobs():
    identity = read("identities.json")
    verification = read("verification.json")
    assert (
        identity["native_session_id"]
        == json.loads((ROOT / "docs/evidence/m05/identities.json").read_text())["session_id"]
    )
    assert identity["sdk"] == "1.0.34" and identity["model"] == "gpt-6.1-sol"
    assert verification["new_model_sends"] == 1 and verification["elapsed_ms"] <= 180000
    assert verification["new_children"] == 0 and verification["new_preview_jobs"] == 0
    assert verification["new_native_processes"] == 1
    assert len(read("public-before.json")["requests"]["items"]) == 3
    assert len(read("public-final.json")["requests"]["items"]) == 4
    assert all(
        r["receipt"]["state"] == "completed" for r in read("public-final.json")["requests"]["items"]
    )


def test_both_real_historical_reopens_geometry_units_texture_and_hashes():
    report = read("history-adoption-completion.json")
    assert len(report["historical_inspections"]) == 2
    for history in report["historical_inspections"]:
        assert history["status"] == "actual_reopen_verified" and not history["saved"]
        assert history["units"] == {"system": "METRIC", "scale_length": 1}
        assert history["hashes_before"] == history["hashes_after"]
        assert {o["ids"]["proscenium_instance_id"] for o in history["instances"]} == {
            "instance-blue",
            "instance-renamed",
        }
        for obj in history["instances"]:
            assert obj["ids"]["proscenium_object_id"] == "object-cube-source"
            assert obj["dimensions_m"] == [1, 1, 1]
        texture = history["dependencies"]["textures"][0]
        assert texture["dimensions"] == [8, 8]
        assert (
            texture["sha256"] == "fcf1e3f027e9b1ea14df033cba5b5134a7db305f9fc69239f517abb1be932568"
        )


def test_old_source_index_reports_jobs_versions_and_owner_choice_unchanged():
    verification = read("verification.json")
    assert verification["head_before"] == verification["head_after"]
    assert verification["index_before"] == verification["index_after"]
    assert all(row["before"] == row["after"] for row in verification["protected_files"].values())
    before, after = read("public-before.json"), read("public-final.json")
    assert before["versions"] == after["versions"]
    assert before["jobs"] == after["jobs"]
    assert before["selections"] == after["selections"]
    for item in ("scene-main", "scene-alternate"):
        version = after["versions"][item]["items"][0]
        validate("VersionRecord", version)
        assert version["source"]["dependencies"] == [
            {"item_id": "asset-cube", "version_id": "asset-cube-v1"}
        ]


def test_real_ui_old_current_and_same_current_reclick_context():
    context = read("same-current-context.json")
    assert context["before"] == context["after"] and context["exactly_preserved"]
    target = context["after"]["context"]["target"]
    validate("FrameTarget", target)
    assert target["origin"] == "blender_render" and target["version_id"] == "asset-cube-v3"
    assert target["source_frame"] == 1
    assert context["after"]["viewport"] == {"width": 1920, "height": 1080, "dpr": 1}
    reopen = read("reopen-observation.json")
    assert reopen["requests_before"] == reopen["requests_after"] == 4
    assert not reopen["automatic_resume_sent"] and reopen["jobs_unchanged"]


def test_failure_is_explicit_and_no_checkpoint_publication_or_second_process():
    report = read("history-adoption-completion.json")
    verification = read("verification.json")
    assert verification["strict_result"] == "remaining"
    assert not verification["adoption_saved"] and not verification["adoption_published"]
    assert report["failure"]["post_process_resolution_check"]["resolved_equality"]
    assert not report["failure"]["post_process_resolution_check"]["lexical_equality"]
    assert not report["failure"]["second_process_attempted"]
    terminal = read("native-terminal.json")
    assert terminal["exit_code"] == 1 and terminal["reaped"]
    assert terminal["available_bytes_before"] >= 4 * 1024**3
    names = {call["toolName"] for call in read("native-tool-invocations.json")}
    assert not names & {
        "task",
        "publish_outputs",
        "checkpoint_sources",
        "submit_generation",
        "request_preview",
    }
    # Canonicalisation is a deterministic check, not another native adoption run.
    root = Path(read("identities.json")["retained_project_root"])
    lexical = root / "scenes/fixture/../../assets/cube/asset.blend"
    canonical = root / "assets/cube/asset.blend"
    assert lexical != canonical and lexical.resolve() == canonical.resolve()


def test_manifest_and_owned_cleanup_without_exported_authentication():
    for name, identity in read("manifest.json").items():
        content = (EVIDENCE / name).read_bytes()
        assert hashlib.sha256(content).hexdigest() == identity["sha256"]
        assert len(content) == identity["bytes"]
        if name.endswith(".json"):
            for secret in ('"csrf_token":', "#pair=", "github_pat_", "ghp_"):
                assert secret not in content.decode()
    cleanup = read("cleanup.json")
    assert cleanup["browser_reaped"] and cleanup["native_proc_absent"]
