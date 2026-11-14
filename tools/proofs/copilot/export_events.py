"""Allowlist synthetic native evidence, excluding API IDs and encrypted reasoning."""

import argparse
import json
from pathlib import Path

DATA_KEYS = {
    "content",
    "messageId",
    "clientTurnId",
    "delivery",
    "turnId",
    "originatingMessageId",
    "toolCallId",
    "parentToolCallId",
    "toolName",
    "arguments",
    "success",
    "model",
    "agentName",
    "agentType",
    "tools",
    "modelSelectionSource",
    "firstDispatchedModel",
    "parentAgentId",
    "workingDirectory",
    "reason",
    "errorType",
    "message",
}
EVENT_TYPES = {
    "user.message",
    "assistant.message",
    "tool.execution_start",
    "tool.execution_complete",
    "subagent.started",
    "subagent.completed",
    "subagent.failed",
    "session.idle",
    "session.resume",
    "session.error",
    "session.warning",
    "abort",
}


def export(events):
    return [
        {
            **{
                key: event[key]
                for key in ("id", "type", "timestamp", "agentId", "parentId")
                if key in event
            },
            "data": {
                key: value
                for key, value in event.get("data", {}).items()
                if key in DATA_KEYS
            },
        }
        for event in events
        if event.get("type") in EVENT_TYPES
    ]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    args.destination.write_text(
        json.dumps(
            {
                "evidence_kind": "allowlisted real native events for synthetic project only",
                "note": "Initial run completed two root requests, then evidence JSON formatting failed. No native request was retried. Callback argument objects were not recovered. Source event archive retained in private temporary runtime.",
                "events": export(json.loads(args.source.read_text())),
            },
            indent=2,
        )
        + "\n"
    )
