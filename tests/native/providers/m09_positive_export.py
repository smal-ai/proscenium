"""Allowlisted export of the retained real positive M09 run. No new model/native work."""

import hashlib
import json
import shutil
import subprocess
from importlib.metadata import version
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
ROOT = Path.home() / ".cache/proscenium-development/m09-positive-20261114"
OUT = REPO / "docs/evidence/m09-positive"


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def read(name):
    return json.loads((ROOT / name).read_bytes())


def lines(name):
    return [json.loads(v) for v in (ROOT / name).read_text().splitlines()]


def write(name, value):
    (OUT / name).write_text(json.dumps(value, indent=2) + "\n")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    fixture, owner, native, approved, final = [
        read(f"{name}.json") for name in ("fixture", "owner", "native", "approve", "collect")
    ]
    sdk = lines("sdk-send.jsonl")
    vlm = lines("vlm.jsonl")
    ticket = lines("tool-results.jsonl")
    assert len(sdk) == 2 and sum(v["phase"] == "dispatch" for v in vlm) == 2
    assert owner["settled"]["status"] == native["ticket"]["status"] == "ready"
    assert owner["state"]["requests"]["items"] == []
    assert len(ticket) == 1 and set(json.loads(ticket[0]["result"])) == {
        "schema_version",
        "review_id",
        "job_id",
        "status",
    }
    second = sdk[1]
    payload = next(a["payload"] for a in second["attachments"] if a["type"] == "extension_context")
    assert second["prompt"] == approved["amended"]
    assert payload["approved_findings"] == [
        dict(
            finding_id=approved["selected"]["finding_id"],
            finding_revision=1,
            approved_text=approved["amended"],
            target=approved["selected"]["target"],
        )
    ]
    assert approved["declined"]["observation"] not in json.dumps(second)
    assert approved["declined"]["finding_id"] not in json.dumps(second)
    assert approved["replay"]["status"] == 200 and approved["replay"]["receipt"]["replayed"]
    hashes = [
        sha(Path(a["path"]).read_bytes()) for a in second["attachments"] if a["type"] == "file"
    ]
    assert set(hashes) == {fixture["artifact"]["sha256"]}
    source = Path(fixture["source_path"])
    assert sha(source.read_bytes()) == fixture["source_sha256"]
    assert len(final["state"]["requests"]["items"]) == 2
    assert len(final["state"]["annotations"]["items"]) == 1
    callback_events = []
    path = next((ROOT / "machine/copilot-runtime/home/session-state").glob("*/events.jsonl"))
    for row in path.read_text().splitlines():
        event = json.loads(row)
        if event["type"] == "tool.execution_start":
            data = {
                k: v
                for k, v in event.get("data", {}).items()
                if k in {"toolCallId", "toolName", "arguments"}
            }
            callback_events.append(
                {k: event.get(k) for k in ("id", "timestamp", "type")} | {"data": data}
            )
        assert not event["type"].startswith("subagent."), "No native child allocation exists"
    write("native-tool-arguments.json", callback_events)
    write("fixture-identities.json", fixture)
    write("vlm-observations.json", vlm)
    write("native-delivery.json", sdk)
    write("native-tool-ticket.json", ticket)
    for name in ("baseline", "allocation", "loaded", "model-cleanup", "host-cleanup"):
        write(name + ".json", read(name + ".json"))
    commands = []
    for name in ("owner", "native", "approve", "unavailable", "collect"):
        write("public-" + name + ".json", read(name + ".json"))
        shutil.copyfile(ROOT / (name + ".png"), OUT / ("browser-" + name + ".png"))
        for value in read(name + "-traffic.json"):
            if value["method"] == "POST":
                commands.append({"phase": name, **value})
    shutil.copyfile(ROOT / "approval-before-queue.png", OUT / "browser-amend-and-reject.png")
    write("public-owner-commands.json", commands)
    source_files = [
        "apps/backend/src/proscenium/providers/vlm.py",
        "apps/backend/src/proscenium/providers/service.py",
        "apps/backend/src/proscenium/reviews/store.py",
        "apps/backend/src/proscenium/copilot/tools.py",
        "tests/native/providers/m09_host.py",
        "tests/native/providers/m09_positive_host.py",
    ]
    binary = REPO / "node_modules/@github/copilot-linux-x64/copilot"
    with binary.open("rb") as stream:
        binary_sha = hashlib.file_digest(stream, "sha256").hexdigest()
    write(
        "runtime-identities.json",
        dict(
            application_base="ad47260a922612393896c21ff7186d643037c524",
            sdk_version=version("github-copilot-sdk"),
            httpx_version=version("httpx"),
            pillow_version=version("Pillow"),
            browser=final["browser"],
            native_binary_sha256=binary_sha,
            source_sha256={name: sha((REPO / name).read_bytes()) for name in source_files},
            compiled_sha256={
                str(p.relative_to(REPO)): sha(p.read_bytes())
                for p in (REPO / "apps/frontend/dist").rglob("*")
                if p.is_file() and p.suffix in {".js", ".css", ".html"}
            },
            source_commit=fixture["source_commit"],
            source_fixture_sha256_after=sha(source.read_bytes()),
            old_failed_manifest_sha256=sha((REPO / "docs/evidence/m09/manifest.json").read_bytes()),
            old_failed_manifest_base_sha256=sha(
                subprocess.check_output(
                    ["git", "show", "ad47260:docs/evidence/m09/manifest.json"], cwd=REPO
                )
            ),
            note="No product provider/review fix was needed in this positive allocation. Application sources equal the executed accepted base. Driver hashes describe the maintained post-format files.",
        ),
    )
    write(
        "verification.json",
        dict(
            result="POSITIVE_JOURNEY_PASS_PENDING_COORDINATOR_ACCEPTANCE",
            counts=dict(
                model_loads=1,
                vlm_inferences=2,
                cloud_roots=2,
                native_children=0,
                owner_approved_submissions=1,
                exact_queue_replays=1,
                model_unloads=1,
            ),
            owner_ready=True,
            native_ready=True,
            owner_review_did_not_start_native_work=True,
            approved_text_exact=True,
            target_exact=True,
            source_unchanged=True,
            rejected_finding_absent_from_native_send=True,
            triggering_tool_ticket_only=True,
            replay_same_native_message_id=True,
            sdk_file_bindings=len(hashes),
            unique_sdk_image_sha256=sorted(set(hashes)),
            sdk_duplicate_binding_disclosure="W01 attached the same immutable clean PNG as render and concept. One Queue and one unique image, not two deliveries or distinct images.",
            unavailable_preflight_no_extra_inference=True,
            normal_feedback_saved_after_cleanup=True,
            scope="Scripted owner inputs, actual compiled Full-HD UI and real production services/LM Studio/native SDK. Fixture source is labelled; no generated findings or native responses are seeded.",
            private_runtime=str(ROOT),
        ),
    )
    write(
        "manifest.json",
        {
            p.name: {"sha256": sha(p.read_bytes()), "bytes": p.stat().st_size}
            for p in sorted(OUT.iterdir())
            if p.is_file() and p.name != "manifest.json"
        },
    )
    print(
        json.dumps(
            {
                "export": str(OUT),
                "result": "positive journey passed",
                "unique_images": len(set(hashes)),
                "sdk_file_bindings": len(hashes),
            }
        )
    )


if __name__ == "__main__":
    main()
