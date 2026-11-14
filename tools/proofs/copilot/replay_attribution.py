"""SQLite correlation proof using captured native events, NOT a live adapter.

This intentionally lives outside application migrations. F00 owns those.
"""

import argparse
import json
import sqlite3
from pathlib import Path


def replay(connection: sqlite3.Connection, capture: dict) -> list[dict]:
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS messages(native_id TEXT PRIMARY KEY, request_id TEXT NOT NULL, agent_id TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS agents(agent_id TEXT PRIMARY KEY, request_id TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS turns(agent_id TEXT, turn_id TEXT, native_id TEXT, PRIMARY KEY(agent_id, turn_id));
        CREATE TABLE IF NOT EXISTS invocations(tool_call_id TEXT PRIMARY KEY, request_id TEXT NOT NULL, agent_id TEXT NOT NULL);
    """)
    # This capture contains several owned sessions. Delimit its first settled
    # work by its own resume marker, not a mutable current-request variable.
    for event in capture["events"]:
        if event["type"] == "session.resume":
            break
        encoded = json.dumps(event, sort_keys=True)
        old = connection.execute(
            "SELECT body FROM events WHERE id=?", (event["id"],)
        ).fetchone()
        if old is not None:
            if old[0] != encoded:
                raise ValueError("Native event identity reused with different body")
            continue
        data = event["data"]
        agent = event.get("agentId", "root")
        with connection:
            connection.execute(
                "INSERT INTO events VALUES(?, ?)", (event["id"], encoded)
            )
            if event["type"] == "user.message":
                request = data.get("clientTurnId")
                if request is None:
                    row = connection.execute(
                        "SELECT request_id FROM agents WHERE agent_id=?", (agent,)
                    ).fetchone()
                    if row is None:
                        raise ValueError("Unattributed native child")
                    request = row[0]
                connection.execute(
                    "INSERT INTO messages VALUES(?, ?, ?)",
                    (data["messageId"], request, agent),
                )
            elif event["type"] == "assistant.message" and data.get(
                "originatingMessageId"
            ):
                connection.execute(
                    "INSERT OR REPLACE INTO turns VALUES(?, ?, ?)",
                    (agent, data["turnId"], data["originatingMessageId"]),
                )
            elif event["type"] == "tool.execution_start":
                row = connection.execute(
                    "SELECT m.request_id FROM turns t JOIN messages m ON m.native_id=t.native_id WHERE t.agent_id=? AND t.turn_id=?",
                    (agent, data["turnId"]),
                ).fetchone()
                if row is None:
                    raise ValueError("Unattributed native tool call")
                connection.execute(
                    "INSERT INTO invocations VALUES(?, ?, ?)",
                    (data["toolCallId"], row[0], agent),
                )
            elif event["type"] == "subagent.started":
                row = connection.execute(
                    "SELECT request_id FROM invocations WHERE tool_call_id=?",
                    (data["toolCallId"],),
                ).fetchone()
                if row is None:
                    raise ValueError("Unattributed delegation")
                connection.execute("INSERT INTO agents VALUES(?, ?)", (agent, row[0]))
    callbacks = []
    for report in capture["reports"]:
        row = connection.execute(
            "SELECT request_id, agent_id FROM invocations WHERE tool_call_id=?",
            (report["tool_call_id"],),
        ).fetchone()
        if row is None or row[1] != (report["agent_id"] or "root"):
            raise ValueError(
                "Callback attribution does not match persisted native event"
            )
        callbacks.append(
            {
                "tool_call_id": report["tool_call_id"],
                "request_id": row[0],
                "agent_id": row[1],
            }
        )
    return callbacks


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    capture = json.loads(args.capture.read_text())["advanced"]
    with sqlite3.connect(args.database) as connection:
        callbacks = replay(connection, capture)
        replay(connection, capture)
        counts = {
            table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("events", "messages", "agents", "invocations")
        }
    args.output.write_text(
        json.dumps(
            {
                "evidence_kind": "post-capture SQLite replay of real native evidence, not live persistence acceptance",
                "replayed_twice_without_duplication": True,
                "counts": counts,
                "callbacks": callbacks,
            },
            indent=2,
        )
        + "\n"
    )
