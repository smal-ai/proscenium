"""Export only the retained M11 cold Stop observation. No SDK call or state change."""

import collections
import hashlib
import json
import sqlite3

from cold_stop_recovery import HEAD, ORIGINAL, REPO, ROOT
from proscenium.contracts import canonical
from proscenium.copilot.launcher import identity
from runtime_recovery_history import sha


def load(path):
    return json.loads(path.read_text())


def main():
    destination = REPO / "docs/evidence/cold-stop-recovery"
    destination.mkdir(parents=True, exist_ok=True)
    result = load(ROOT / "result.json")
    hashes = load(ROOT / "before-hashes.private.json")
    assert all(sha(ROOT / "before" / relative) == expected for relative, expected in hashes.items())
    observation = next(o for o in result["observations"] if o["status"] == "applied")
    process = result["process"]
    process["parent_same_identity_alive"] = (
        identity(process["parent_pid"]) == process["parent_start"]
    )
    assert not process["same_identity_alive"] and not process["parent_same_identity_alive"]
    result["control_rpc_counts"] = dict(
        collections.Counter(r["method"] for r in observation["snapshot"]["rpc"])
    )
    result["counter_scope"] = (
        "counts.aborts/pauses are old session-wrapper counters; control_rpc_counts records the actual typed RPCs used here."
    )
    result["question_byte_identical"] = result["questions_before"] == result["questions_after"]
    result["permission_stage_byte_identical"] = result["stages_before"] == result["stages_after"]
    assert result["question_byte_identical"] and result["permission_stage_byte_identical"]
    before = load(ROOT / "before.private.json")
    after = load(ROOT / "after.private.json")
    result["new_unity_publication"] = any(
        v.get("kind") == "unity_result" for v in after["records"].get("versions", [])
    )
    result["native_goal_completion_claimed"] = False
    for event in result["new_public_events"]:
        submission = event.get("payload", {}).get("submission", {})
        if "text" in submission:
            event["source_event_sha256"] = hashlib.sha256(canonical(event)).hexdigest()
            event["submission_text_sha256"] = hashlib.sha256(
                submission.pop("text").encode()
            ).hexdigest()
            event["submission_text_redacted"] = True
    result["new_request_ids"] = sorted(
        {r["request_id"] for r in after["records"]["requests"]}
        - {r["request_id"] for r in before["records"]["requests"]}
    )
    assert not result["new_unity_publication"] and result["new_request_ids"] == []
    db = sqlite3.connect(f"file:{ROOT}/before/project/.proscenium/state.sqlite3?mode=ro", uri=True)
    try:
        receipt = json.loads(
            db.execute(
                "SELECT result FROM operations WHERE scope='sources.checkpoint' AND id='m11-unity-source-checkpoint'"
            ).fetchone()[0]
        )
    finally:
        db.close()
    assert receipt["source_commit"] == HEAD
    for item in receipt["files"]:
        assert sha(ORIGINAL / "project" / item["path"]) == item["sha256"]
    result["checkpoint_receipt_files_verified"] = True
    (destination / "checkpoint.json").write_text(json.dumps(receipt, indent=2) + "\n")
    (destination / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    (destination / "rpc-observation.json").write_text(json.dumps(observation, indent=2) + "\n")
    (destination / "manifest.json").write_text(
        json.dumps(
            {
                p.name: sha(p)
                for p in destination.iterdir()
                if p.is_file() and p.name != "manifest.json"
            },
            indent=2,
        )
        + "\n"
    )
    print(
        json.dumps(
            dict(
                ready=result["ready_for_original_continue"],
                stop=result["stop_receipt"]["state"],
                work=result["work_after"]["state"],
                revision=result["work_after"]["state_revision"],
                sends=result["counts"]["sends"],
                owned_processes_closed=True,
            )
        )
    )


if __name__ == "__main__":
    main()
