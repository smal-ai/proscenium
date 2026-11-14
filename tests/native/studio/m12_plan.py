"""Prepare private browser inputs and one explicit restored-project marker."""

import json
import socket
import sqlite3
import sys
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from m12_prepare import ORIGINAL, PROJECT, REPO, SESSION

sys.path.insert(0, str(REPO))
from tools.operations.recovery import write_json


def main():
    root = Path(sys.argv[1])
    assert (root / "prepared.json").exists()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    config = json.loads((ORIGINAL / "config.json").read_text())
    config.update(
        data_directory=str(root / "restored/machine"),
        origin=f"http://127.0.0.1:{port}",
        frontend_directory=str(REPO / "apps/frontend/dist"),
        services=[],
        provider_options={},
    )
    config["runtime"] = {
        "cli_path": str(REPO / "node_modules/@github/copilot-linux-x64/copilot"),
        "lead_model": "gpt-6.1-sol",
        "worker_model": "gpt-6.1-sol",
        "profile_id": "authoring",
        "skill_directories": [str(REPO / "agent/skills")],
        "mcp_servers": {},
        "worker_tools": ["view", "bash"],
    }
    config["rendering"]["claim_path"] = str(root / "unused-render.lock")
    write_json(root / "config.json", config)
    token = str(uuid4())
    write_json(
        root / "restored/project/m12-recovery-marker.json",
        {"marker_token": token, "purpose": "Read-only M12 relocated-scope proof"},
    )
    with closing(
        sqlite3.connect(
            (root / "restored/project/.proscenium/state.sqlite3").as_uri() + "?mode=ro", uri=True
        )
    ) as db:
        requests = [
            json.loads(row[0])
            for row in db.execute("SELECT body FROM records WHERE namespace='requests'")
        ]
        records = {
            row[0]: json.loads(row[1])
            for row in db.execute("SELECT id,body FROM records WHERE namespace='contexts'")
        }
        original = next(
            records[r["submission"]["context_id"]]
            for r in requests
            if records[r["submission"]["context_id"]]["annotations"]
        )
    contexts = []
    for number in (1, 2):
        value = {
            **original,
            "context_id": str(uuid4()),
            "captured_at": datetime.now(UTC).isoformat(),
        }
        if number == 2:
            value.update(annotations=[], attachments=[], approved_findings=[])
        contexts.append(value)
    write_json(
        root / "plan-private.json",
        {
            "session_id": SESSION,
            "project_id": PROJECT,
            "origin": config["origin"],
            "python": str(REPO / ".venv/bin/python"),
            "request_ids": [str(uuid4()), str(uuid4())],
            "contexts": contexts,
            "marker_token": token,
            "original_native_message_ids": [r["receipt"]["native_message_id"] for r in requests],
            "prompts": [
                "Read-only recovery verification of this same conversation. Do not edit any file or scene, author, render, generate, install, alter services, or delegate. "
                "Use only read tools to read ./m12-recovery-marker.json and obtain the actual working directory with pwd. "
                "Then reply with compact JSON containing marker_token, cwd, previous_visual_correction {object,before_height,after_height,preserved_object}, "
                "and annotation_pinned_version. For the historical facts use our earlier PUBLIC discussion and the attached image/annotation. "
                "Do not read runtime databases, prompts, private instructions, credentials or other roots. The old annotation is historical feedback: DO NOT execute its edit instruction.",
                "Read-only transport fault probe in this same discussion. Do not use tools, edit files, delegate, or alter services. Reply exactly M12_TRANSPORT_ONCE and finish.",
            ],
        },
    )
    print(
        json.dumps(
            {"root": str(root), "project_id": PROJECT, "session_id": SESSION, "cloud_sends": 0}
        )
    )


if __name__ == "__main__":
    main()
