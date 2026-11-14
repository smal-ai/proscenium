"""Export only allowlisted real M09 records. No inference, browser or model lifecycle calls."""

import hashlib
import json
import shutil
import subprocess
from importlib.metadata import version
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
ROOT = Path(__file__).with_name("m09_runtime")
OUT = REPO / "docs/evidence/m09"


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def read(name):
    return json.loads((ROOT / name).read_bytes())


def lines(name):
    return [json.loads(row) for row in (ROOT / name).read_text().splitlines()]


def write(name, value):
    (OUT / name).write_text(json.dumps(value, indent=2) + "\n")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    fixture = read("fixture.json")
    actual = read("collect.json")
    vlm = []
    for row in lines("vlm.jsonl"):
        value = {k: v for k, v in row.items() if k != "body"}
        if row["phase"] == "response":
            body = row["body"]
            value.update(
                id=body.get("id"), model=body.get("model"), usage=body.get("usage"), choices=[]
            )
            for choice in body.get("choices", []):
                message = choice.get("message", {})
                value["choices"].append(
                    dict(
                        finish_reason=choice.get("finish_reason"),
                        content=message.get("content"),
                        reasoning_present=bool(message.get("reasoning_content")),
                    )
                )
        vlm.append(value)
    assert sum(v["phase"] == "dispatch" for v in vlm) == 2
    native = lines("sdk-send.jsonl")
    assert len(native) == 1, (
        "No approved finding existed. A second cloud root would be false acceptance."
    )
    tool = lines("tool-results.jsonl")
    assert len(tool) == 1 and set(json.loads(tool[0]["result"])) == {
        "schema_version",
        "review_id",
        "job_id",
        "status",
    }
    source = Path(fixture["source_path"])
    assert sha(source.read_bytes()) == fixture["source_sha256"]
    assert all(not value["items"] for value in actual["findings"].values())
    original = REPO / "docs/evidence/m01/clean-v2.png"
    project_artifact = (
        Path(fixture["project"]["path"])
        / ".proscenium/artefacts/sha256"
        / fixture["artifact"]["sha256"]
        / "content"
    )
    assert (
        sha(original.read_bytes())
        == sha(project_artifact.read_bytes())
        == fixture["artifact"]["sha256"]
    )
    write("fixture-identities.json", fixture)
    binary = REPO / "node_modules/@github/copilot-linux-x64/copilot"
    with binary.open("rb") as stream:
        binary_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
    baseline_source = subprocess.check_output(
        ["git", "show", "b1fa32c:apps/backend/src/proscenium/providers/vlm.py"], cwd=REPO
    )
    write(
        "runtime-identities.json",
        dict(
            sdk_version=version("github-copilot-sdk"),
            httpx_version=version("httpx"),
            pillow_version=version("Pillow"),
            native_binary_sha256=binary_sha256,
            browser=actual["browser"],
            initial_vlm_source_sha256=sha(baseline_source),
            second_call_vlm_source=read("executed-fix-source.json"),
            final_vlm_source_sha256=sha(
                (REPO / "apps/backend/src/proscenium/providers/vlm.py").read_bytes()
            ),
            execution_scope="Initial owner call used accepted baseline. Native call used only the reasoning-off fix. Role-bound output schema was added afterwards and has offline coverage only.",
            loader_command="uv run --script /home/quintin/.config/opencode/skills/lmstudio/scripts/lms-load.py --config /home/quintin/.config/llmster/models.toml google/gemma-4-e4b",
            loader_exit_code=0,
        ),
    )
    write("vlm-responses-redacted.json", vlm)
    write("native-delivery.json", native)
    write("native-tool-ticket.json", tool)
    for name in (
        "baseline.json",
        "loaded.json",
        "model-cleanup.json",
        "executed-fix-source.json",
        "owner-vlm-summary.json",
        "host-cleanup.json",
        "post-unavailable-inventory.json",
    ):
        write(name, read(name))
    for name in ("owner", "native", "unavailable", "refresh", "collect"):
        write(f"public-{name}.json", read(f"{name}.json"))
        shutil.copyfile(ROOT / f"{name}.png", OUT / f"browser-{name}.png")
    commands = []
    for name in ("owner", "native", "unavailable"):
        for value in read(f"{name}-traffic.json"):
            if value["method"] == "POST":
                commands.append({"phase": name, **value})
    write("public-owner-commands.json", commands)
    events = []
    for value in lines("native-events.jsonl"):
        event = value.get("event", {})
        if event.get("type") in {"session.idle", "session.error"}:
            events.append({"at": value["at"], "session_id": value["session_id"], "event": event})
    write("native-settlement.json", events)
    write(
        "result.json",
        dict(
            result="BLOCKED_NOT_COMPLETE",
            application_base="b1fa32c8a73d5aca8243fa82e65837a8acaba75c",
            counts=dict(
                model_loads=1,
                vlm_inferences=2,
                cloud_roots=1,
                native_children=0,
                approved_submissions=0,
                model_unloads=1,
            ),
            owner_review="Failed safely: 512 tokens exhausted on reasoning, empty content.",
            native_review="Failed safely: model attributed measured evidence to image, not supplied telemetry.",
            actual_runtime_coverage="Both origins reached actual LM Studio. Native root requested one real review and received only its ticket.",
            approval="NOT_RUN: no valid stored findings. No fabricated approval, rejection or native handoff.",
            unavailable="After exact owned Gemma unload, owner UI requested another review. Metadata preflight failed capability_unavailable without third inference. Image review remained available.",
            source_unchanged=True,
            original_image_sha256=fixture["artifact"]["sha256"],
            source_fixture_sha256=fixture["source_sha256"],
            private_runtime=str(ROOT),
            runtime_hooks="Observation-only real HTTP transport, SDK send/event and tool-result wrappers. No mocked provider response or altered native tool return.",
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
                "result": "BLOCKED_NOT_COMPLETE",
                "private_reasoning_exported": False,
            }
        )
    )


if __name__ == "__main__":
    main()
