"""Fresh independent fixture. Does not read or retry any old M12 root."""

import json
import socket
import sys
from pathlib import Path
from uuid import uuid4

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
from tools.operations.recovery import write_json  # noqa: E402 -- maintained repository tool
from tools.operations.safety import private_directory  # noqa: E402


def main():
    root = Path(sys.argv[1]).absolute()
    private_directory(root)
    for name in ("t", "commands", "receipts"):
        (root / name).mkdir(mode=0o700)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    write_json(
        root / "config.json",
        {
            "data_directory": str(root / "d"),
            "origin": origin,
            "frontend_directory": str(REPO / "apps/frontend/dist"),
            "services": [],
            "runtime": {
                "cli_path": str(REPO / "node_modules/@github/copilot-linux-x64/copilot"),
                "lead_model": "gpt-6.1-sol",
                "worker_model": "gpt-6.1-sol",
                "profile_id": "authoring",
                "mcp_servers": {},
                "skill_directories": [str(REPO / "agent/skills")],
            },
            "profiles": [
                {
                    "profile_id": "authoring",
                    "name": "Read-only ACK allocation",
                    "lead_model_id": "gpt-6.1-sol",
                    "worker_model_ids": [],
                    "scope_status": "project_scoped",
                }
            ],
        },
    )
    write_json(
        root / "plan-private.json",
        {
            "origin": origin,
            "request_id": str(uuid4()),
            "context_id": str(uuid4()),
            "prompt": "Read-only transport probe. Do not use tools, delegate, edit files or alter services. Reply exactly ACK_BOUNDARY_OK and stop.",
            "python": str(REPO / ".venv/bin/python"),
            "project_path": str(root / "p"),
        },
    )
    print(json.dumps({"root": str(root), "sends": 0, "limit": 1}))


if __name__ == "__main__":
    main()
