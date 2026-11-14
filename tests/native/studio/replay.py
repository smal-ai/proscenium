"""Trusted coordinator replay fixture. Retains the exact persisted tool authority/body.

This issues no new work/invocation or owner selection. The ephemeral capability
uses the original admitted invocation's subject, work and epoch, then is revoked.
It is not a model-facing API or a substitute for native admission.
"""

import json
import sqlite3
import sys
from pathlib import Path

import httpx
from proscenium.auth import Auth, Caller
from proscenium.db.store import Store

root, publication_id = Path(sys.argv[1]), sys.argv[2]
connection = sqlite3.connect(f"file:{root}/project/.proscenium/state.sqlite3?mode=ro", uri=True)


def record(namespace, identity):
    row = connection.execute(
        "SELECT body FROM records WHERE namespace=? AND id=?", (namespace, identity)
    ).fetchone()
    assert row is not None
    return json.loads(row[0])


def identities():
    result = {}
    for namespace in ("versions", "jobs", "attempts", "frames", "publications"):
        result[namespace] = [
            row[0]
            for row in connection.execute(
                "SELECT id FROM records WHERE namespace=? ORDER BY id", (namespace,)
            )
        ]
    result["launch_claims"] = sorted(
        str(p.relative_to(root)) for p in (root / "project/.proscenium/jobs").rglob("launch.claim")
    )
    return result


publication = record("publications", publication_id)
invocation = record("invocations", publication["invocation_id"])
assert invocation["name"] == "publish_outputs"
assert invocation["work_id"] == publication["work_id"]
body = publication["submission"]
caller = Caller(
    "tool",
    invocation["native_id"],
    body["project_id"],
    invocation["work_id"],
    invocation["owner_epoch"],
    operations=("publishOutputs",),
    invocation_id=invocation["invocation_id"],
)
auth = Auth(Store(root / "machine/registry.sqlite3"))
token = auth.issue_capability(caller)
before = identities()
info = json.loads((root / "processes.json").read_text())
try:
    response = httpx.post(
        f"{info['origin']}/internal/v1/publications",
        json=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Origin": info["origin"],
            "Idempotency-Key": publication_id,
        },
        timeout=15,
    )
finally:
    auth.revoke(token)
after = identities()
result = {
    "fixture": "trusted original persisted invocation replay, not fresh authority",
    "publication_id": publication_id,
    "invocation": invocation,
    "original_receipt": publication["receipt"],
    "http_status": response.status_code,
    "response": response.json(),
    "before": before,
    "after": after,
    "unchanged": before == after,
}
(root / "publication-replay.json").write_text(json.dumps(result, indent=2))
assert response.status_code == 200, result
assert result["response"]["replayed"] is True
assert before == after
assert result["response"]["versions"] == publication["receipt"]["versions"]
assert result["response"]["job_ids"] == publication["receipt"]["job_ids"]
print(
    json.dumps(
        {
            "status": response.status_code,
            "unchanged": before == after,
            "replayed": result["response"]["replayed"],
        }
    )
)
