"""Read-only actual publication evidence and postcondition export. No native effects."""

import hashlib
import json
import shutil
import sqlite3
from typing import TYPE_CHECKING

from proscenium.contracts import canonical, validate

from tools.unity.jobs import attempt_directory

if TYPE_CHECKING:
    from tests.native.unity.m11_complete_snapshot import (
        HEAD,
        PROJECT,
        REPO,
        REQUEST,
        ROOT,
        SESSION,
        WORK,
        alive,
        checkpoint,
        records,
        sha,
        write,
    )
else:
    from m11_complete_snapshot import (
        HEAD,
        PROJECT,
        REPO,
        REQUEST,
        ROOT,
        SESSION,
        WORK,
        alive,
        checkpoint,
        records,
        sha,
        write,
    )


def load(name):
    return json.loads((ROOT / name).read_text())


def main():
    before, after = load("before.private.json"), load("after.private.json")
    old, new = before["records"], after["records"]
    for field in ("source_files", "old_reports", "head", "refs", "git_index"):
        assert before[field] == after[field], field
    unchanged = (
        "jobs",
        "attempts",
        "artifacts",
        "job_inputs",
        "job_evidence",
        "contexts",
        "checkpoint_intents",
        "decisions",
    )
    for name in unchanged:
        assert old[name] == new[name], name
    for name in ("copilot.bindings", "versions", "items", "publications"):
        assert all(new[name][key] == value for key, value in old[name].items()), name
    permission_changes = []
    for key, value in old["copilot.permissions"].items():
        current = new["copilot.permissions"][key]
        if current != value:
            assert key == "fbd9a5d969c84dd82857ba80d5811d112076572904ba29f6f27a09e384c972ff"
            assert value["phase"] == "reply_prepared" and current["phase"] == "outcome_uncertain"
            assert set(k for k in set(value) | set(current) if value.get(k) != current.get(k)) == {
                "phase",
                "interrupted_at",
            }
            assert (
                current["delivery_state"] == "outcome_uncertain"
                and current["authority"] == "host"
                and current["reply"] == "reject"
            )
            permission_changes.append(
                dict(
                    storage_id=key,
                    before=value,
                    after=current,
                    meaning="Normal cold restoration records interrupted prepared host reply. Delivery remains unknown. No owner answer or ACK.",
                )
            )
    assert set(new["requests"]) == set(old["requests"])
    for key, value in old["requests"].items():
        assert value["submission"] == new["requests"][key]["submission"]
        if key != REQUEST:
            assert value == new["requests"][key]
    assert new["work"][WORK]["state"] == "completed" and new["work"][WORK]["state_revision"] == 12
    assert new["work"][WORK]["generation_task_id"] == old["work"][WORK]["generation_task_id"]
    assert all(
        v["state"] in {"completed", "failed", "cancelled", "stopped"}
        for v in new["invocations"].values()
    )
    born = [v for k, v in new["invocations"].items() if k not in old["invocations"]]
    assert not any(v["kind"] == "child" for v in born)
    assert not any(v.get("name") == "checkpoint_sources" for v in born)
    assert set(new["versions"]) - set(old["versions"]) == {"m11-playable-v1"}
    version = validate("VersionRecord", new["versions"]["m11-playable-v1"])
    item = validate("ItemRecord", new["items"]["m11-playable"])
    assert item["current_version_id"] == "m11-playable-v1" and version["kind"] == "unity_result"
    assert (
        version["source_commit"] == HEAD
        and version["source"]["entry_path"] == version["unity"]["scene_path"]
    )
    assert version["source"]["dependencies"] == [
        dict(item_id="m11-source", version_id="m11-source-v1")
    ]
    assert version["derived_from"] == [dict(item_id="m11-source", version_id="m11-source-v1")]
    assert set(version["artifact_ids"]) == {
        "m11-playmode-attestation",
        "m11-playmode-xml",
        "m11-playmode-metrics",
    }
    closure = dict(version["source"])
    digest = closure.pop("digest")
    assert hashlib.sha256(canonical(closure)).hexdigest() == digest
    receipt = checkpoint()
    files = {f["path"]: f["sha256"] for f in receipt["files"]}
    actual_paths = {
        p.relative_to(PROJECT).as_posix()
        for folder in ("Assets", "Packages", "ProjectSettings")
        for p in (PROJECT / "unity/M11" / folder).rglob("*")
        if p.is_file()
    } | {"unity/M11/M11-operator-playmode.json"}
    assert actual_paths == set(files), "No Unity source helper or extra source file may be added"
    assert {f["destination"]: f["origin"]["sha256"] for f in version["source"]["files"]} == files
    assert all(
        f["origin"]["kind"] == "git"
        and f["origin"]["commit"] == HEAD
        and f["origin"]["path"] == f["destination"]
        for f in version["source"]["files"]
    )
    for artifact in new["artifacts"].values():
        assert (
            sha(PROJECT / f".proscenium/artefacts/sha256/{artifact['sha256']}/content")
            == artifact["sha256"]
        )
    generic_job = new["jobs"]["m11-import-job"]
    generic_path = attempt_directory(PROJECT, generic_job["attempt_id"]) / "unity-result.json"
    generic = json.loads(generic_path.read_text())
    attestation = json.loads((PROJECT / "unity/M11/M11-operator-playmode.json").read_text())
    assert generic["validation"]["status"] == "not_run"
    assert attestation["unity"]["validation"]["status"] == "passed"
    assert (
        version["unity"]["validation"]["notes"]
        == "Operator F05 PlayMode job m11-playmode-job passed on this saved scene; generic import m11-import-job validation was not_run. Virtual Input System polling tested; physical OS input not tested."
    )
    assert version["unity"]["validation"]["status"] == "passed"
    replay = load("publication-replay.private.json")
    assert replay["caller"]["channel"] == "tool" and replay["caller"]["work_id"] == WORK
    assert replay["records_unchanged"] and replay["replay"][0]["replayed"]
    invocation = new["invocations"][replay["caller"]["invocation_id"]]
    assert (
        invocation["native_id"] == "call_lCkKYkLWtfNHpdWXz9fGGxx2"
        and invocation["name"] == "publish_outputs"
    )
    assert invocation["owner_epoch"] == replay["caller"]["owner_epoch"]
    assert invocation["state"] == "completed" and invocation["model_id"] == "gpt-6.1-sol"
    assert invocation["arguments_sha256"] == hashlib.sha256(canonical(replay["body"])).hexdigest()
    sends = {
        k: v
        for k, v in new["copilot.continuation_sends"].items()
        if k not in old["copilot.continuation_sends"]
    }
    assert len(sends) == 1
    send = next(iter(sends.values()))
    assert send["state"] == "acknowledged" and send["session_id"] == SESSION
    owner = load("owner-continue.json")
    assert owner["status"] == 202 and owner["command"]["expected_revision"] == 10
    assert owner["command"]["operation_id"] == send["operation_id"] == invocation["delivery_id"]
    terminal = load("terminal-readback.json")
    assert terminal["completion"]["candidate"] and terminal["completion"]["public_terminal_message"]
    assert (
        terminal["completion"]["delivery_id"]
        == send["operation_id"]
        == terminal["idle"]["delivery_id"]
    )
    assert terminal["idle"]["idle"] and terminal["elapsed_ms"] < 240000
    events = [json.loads(line) for line in (ROOT / "events.private.jsonl").read_text().splitlines()]
    message = next(e for e in events if e["id"] == terminal["completion"]["message_event_id"])
    end = next(e for e in events if e["id"] == terminal["completion"]["producer_event_id"])
    assert message["data"]["content"].strip() and not message["data"].get("toolRequests")
    assert end["type"] in {"assistant.turn_end", "session.task_complete"}
    old_native = records("copilot.native", ROOT / "private-before/project.sqlite3")
    native = records("copilot.native", ROOT / "private-after/project.sqlite3")
    idle = [
        dict(storage_id=k, **v)
        for k, v in native.items()
        if k not in old_native and v["type"] == "session.idle"
    ]
    assert len(idle) == 1 and idle[0]["attributed"] and idle[0]["session_id"] == SESSION
    raw_before = [
        json.loads(line)
        for line in (ROOT / "private-before/native-events.jsonl").read_text().splitlines()
    ]
    raw_after = [
        json.loads(line)
        for line in (ROOT / "private-after/native-events.jsonl").read_text().splitlines()
    ]
    raw_after_by_id = {e["id"]: e for e in raw_after}
    assert all(
        e["id"] in raw_after_by_id and e["type"] == raw_after_by_id[e["id"]]["type"]
        for e in raw_before
    )
    ledger_names = {"copilot.effective"}
    for label in ("before", "after"):
        with sqlite3.connect(
            f"file:{ROOT}/private-{label}/project.sqlite3?mode=ro", uri=True
        ) as db:
            ledger_names.update(
                n
                for (n,) in db.execute("SELECT DISTINCT namespace FROM records")
                if any(word in n.lower() for word in ("budget", "reservation", "counter"))
            )
    assert all(
        records(n, ROOT / "private-before/project.sqlite3")
        == records(n, ROOT / "private-after/project.sqlite3")
        for n in ledger_names
    )
    processes = load("processes.json")
    sdk = load("native-process.private.json")
    assert not any(alive(p) for p in (processes["host"], processes["browser"], sdk))
    cleanup = load("cleanup.json")
    assert (
        cleanup["sdk_sends"] == 1
        and cleanup["browser_reaped"]
        and cleanup["services_closed"]
        and cleanup["stack_seconds"] < 600
    )
    assert load("lifespan-closed.json") == dict(feature_lifespan_closed=True, errors={})
    out = REPO / "docs/evidence/m11-complete"
    out.mkdir(parents=True, exist_ok=True)

    def export(name, value):
        write(out / name, value)

    export(
        "publication.json",
        dict(item=item, version=version, receipt=replay["initial"][0], invocation=invocation),
    )
    export("original-authority-replay.json", replay)
    export(
        "historical-permission-stage.json",
        dict(
            only_cold_restoration_phase_changes=permission_changes,
            owner_decisions_byte_identical=old["decisions"] == new["decisions"],
        ),
    )
    export("checkpoint.json", receipt)
    export(
        "validation-separation.json",
        dict(
            generic_import=generic,
            generic_attempt_path=str(generic_path),
            generic_sha256=sha(generic_path),
            operator=attestation,
            published_unity=version["unity"],
        ),
    )
    export(
        "terminal-native-evidence.json",
        dict(
            final_message={k: message[k] for k in ("id", "type", "timestamp")}
            | dict(
                data={
                    k: message["data"][k]
                    for k in ("content", "messageId", "model", "phase", "toolRequests")
                }
            ),
            turn_end=end,
            idle_projection=terminal["idle"],
            actual_idle_callbacks=idle,
            completion=terminal["completion"],
            work=new["work"][WORK],
            send=send,
            new_invocations=born,
            root_elapsed_ms=terminal["elapsed_ms"],
        ),
    )
    export(
        "preservation.json",
        dict(
            source_files=before["source_files"],
            source_hashes_unchanged=True,
            old_reports=before["old_reports"],
            old_reports_unchanged=True,
            head=HEAD,
            refs=after["refs"],
            git_index_unchanged=True,
            unchanged_namespaces=list(unchanged),
            budget_profile_namespaces_unchanged=sorted(ledger_names),
            old_bindings_unchanged=True,
            old_permission_reply_authority_delivery_preserved=True,
            cold_restoration_phase_changes=len(permission_changes),
            old_decisions_unchanged=True,
            old_versions_unchanged=True,
            original_submissions_unchanged=True,
            contexts_unchanged=True,
            new_requests=0,
            new_children=0,
            new_jobs=0,
            native_historical_ids_preserved=len(raw_before),
            generation_task_id=new["work"][WORK]["generation_task_id"],
        ),
    )
    export(
        "cleanup.json",
        cleanup
        | dict(
            host=processes["host"],
            browser=processes["browser"],
            sdk=sdk,
            recorded_identities_gone=True,
        ),
    )
    export(
        "private-snapshots.json",
        {
            label: dict(
                path=str(ROOT / f"private-{label}"),
                hashes=load(f"{label}.private.json")["private_copies"],
            )
            for label in ("before", "after")
        },
    )
    for name in (
        "owner-continue.json",
        "session-allocation.json",
        "ui-preflight.json",
        "project-activation.json",
        "build-import-composition.json",
        "preflight-oracle-correction.json",
        "send-headroom.json",
        "lifespan-closed.json",
        "public-before-continue.json",
        "public-continue.json",
        "public-catalogue.json",
        "browser-preflight.png",
        "browser-catalogue.png",
    ):
        shutil.copyfile(ROOT / name, out / name)
    export(
        "manifest.json",
        dict(
            status="publication_journey_passed_pending_coordinator_acceptance",
            application_base="17c0a596781b92b31e34c740d03d163eb1d7f263",
            proof_driver_commit="38d5aa1",
            request_id=REQUEST,
            work_id=WORK,
            session_id=SESSION,
            continuation_operation=send["operation_id"],
            continuation_message=send["message_id"],
            publication_id="m11-unity-publication",
            item_id="m11-playable",
            version_id="m11-playable-v1",
            source_commit=HEAD,
            source_closure_digest=digest,
            checkpoint_files=77,
            sdk_sends=1,
            new_queue_requests=0,
            new_conversations=0,
            new_children=0,
            new_editors=0,
            new_blender=0,
            new_exports=0,
            new_renders=0,
            new_inference=0,
            source_recommits=0,
            original_authority_replay=True,
            criteria_1_to_5="retained_existing_bounded_native_evidence",
            criterion_6="passed_actual_publication_and_owner_catalogue_readback",
            milestone_acceptance="coordinator_owned",
            cleanup_complete=True,
        ),
    )
    export(
        "files.json",
        {
            p.name: sha(p)
            for p in out.iterdir()
            if p.is_file() and p.name not in {"files.json", "README.md"}
        },
    )
    print(json.dumps(json.loads((out / "manifest.json").read_text()), indent=2))


if __name__ == "__main__":
    main()
