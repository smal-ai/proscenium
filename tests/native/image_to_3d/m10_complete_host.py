"""Fixture constraints around the normal production host, not a replacement SDK path."""

import copy
import json
import runpy
import sys
from dataclasses import asdict
from unittest.mock import patch

from copilot import CopilotClient, CopilotSession
from m10_complete_snapshot import ROOT, SESSION
from m10_continue_snapshot import write
from proscenium.catalogue.service import Catalogue


def main():
    original_resume = CopilotClient.resume_session
    original_send = CopilotSession.send
    original_publish = Catalogue.publish
    resumes = 0

    async def no_create(*args, **kwargs):
        raise RuntimeError("No new native conversation is authorised")

    async def allocated_resume(client, session_id, **kwargs):
        nonlocal resumes
        assert session_id == SESSION
        resumes += 1
        kwargs.update(excluded_tools=["task"], custom_agents=[], included_builtin_agents=[])
        write(
            ROOT / "session-allocation.json",
            {
                "session_id": session_id,
                "resumes": resumes,
                "creates": 0,
                "excluded_tools": ["task"],
                "custom_agents": [],
                "included_builtin_agents": [],
                "permission_handler_preserved": kwargs.get("on_permission_request") is not None,
                "continue_pending_work": kwargs.get("continue_pending_work"),
                "model": kwargs.get("model"),
                "memory": kwargs.get("memory"),
                "enable_session_store": kwargs.get("enable_session_store"),
            },
        )
        return await original_resume(client, session_id, **kwargs)

    async def allocated_send(session, prompt, **kwargs):
        assert session.session_id == SESSION
        assert prompt == (ROOT / "prompt.txt").read_text(), (
            "Only the new explicit follow-up is authorised"
        )
        with (ROOT / "sdk-send-claimed.json").open("x") as output:
            json.dump(
                {"session_id": SESSION, "sends": 1, "kind": "new_explicit_owner_follow_up"}, output
            )
        result = await original_send(session, prompt, **kwargs)
        write(ROOT / "sdk-send-ack.json", {"native_message_id": result, "session_id": SESSION})
        return result

    def observed_publish(catalogue, caller, body):
        # Initial effect is ONLY the native model's ordinary tool call.
        result = original_publish(catalogue, caller, body)
        if any(row["version_id"] == "m10-asset-v2" for row in body["items"]):
            record = {
                "caller": asdict(caller),
                "body": copy.deepcopy(body),
                "initial": result,
                "replay_origin": "Fixture observer reuses exact original native tool authority/body once after actual model publication",
            }
            marker = ROOT / "publication-replay-claimed.json"
            with marker.open("x") as output:
                json.dump(
                    {
                        "publication_id": body["publication_id"],
                        "invocation_id": caller.invocation_id,
                    },
                    output,
                )
            try:
                with catalogue.services.registry.project_store(body["project_id"]).transaction(
                    write=False
                ) as tx:
                    before = {
                        name: tx.all(name)
                        for name in ("versions", "jobs", "job_inputs", "publications")
                    }
                repeated = original_publish(catalogue, caller, body)
                with catalogue.services.registry.project_store(body["project_id"]).transaction(
                    write=False
                ) as tx:
                    after = {name: tx.all(name) for name in before}
                record.update(replay=repeated, no_mutation=before == after)
            except Exception as error:
                record["replay_blocker"] = f"{type(error).__name__}: {error}"
            write(ROOT / "publication-replay.json", record)
        return result

    with (
        patch.object(CopilotClient, "create_session", no_create),
        patch.object(CopilotClient, "resume_session", allocated_resume),
        patch.object(CopilotSession, "send", allocated_send),
        patch.object(Catalogue, "publish", observed_publish),
    ):
        sys.argv = ["proscenium", "--config", str(ROOT / "config.private.json"), "--pair"]
        runpy.run_module("proscenium", run_name="__main__")


if __name__ == "__main__":
    main()
