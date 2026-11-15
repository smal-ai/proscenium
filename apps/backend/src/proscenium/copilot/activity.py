"""Bounded owner-visible failure diagnostics, never raw tool output or reasoning."""

import json
import logging
import re
from typing import Any

from proscenium.db import Transaction
from proscenium.db.store import now

from .outcomes import get_tool_outcome

MAX_MESSAGE = 2048
REJECTIONS = {"rejected", "reject", "permission_denied", "denied-interactively-by-user"}


def safe_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", value)
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text)
    text = re.sub(r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}", "[redacted]", text)
    text = re.sub(r"(?i)\bBearer\s+[^\s,;]+", "Bearer [redacted]", text)
    text = re.sub(
        r"(?i)\b(api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*[^\s,;]+",
        r"\1=[redacted]",
        text,
    )
    text = re.sub(r"(https?://)[^\s/@]+:[^\s/@]+@", r"\1[redacted]@", text)
    text = text.strip()
    return text if len(text) <= MAX_MESSAGE else text[:MAX_MESSAGE] + "…"


def error_fields(data: dict[str, Any]) -> dict[str, str]:
    error = data.get("error")
    if isinstance(error, str):
        return {"message": safe_text(error)}
    if isinstance(error, dict):
        return {
            name: value for name in ("message", "code") if (value := safe_text(error.get(name)))
        }
    return {}


def shell_exit_code(data: dict[str, Any]) -> int | None:
    shell = data.get("shellExecution")
    value = shell.get("exitCode") if isinstance(shell, dict) else None
    return value if type(value) is int else None


def diagnostic_summary(message: Any, issues: Any) -> str:
    parts = [safe_text(message)]
    if isinstance(issues, list):
        for issue in issues[:5]:
            if isinstance(issue, dict):
                location = safe_text(issue.get("path")) or safe_text(issue.get("phase"))
                detail = safe_text(issue.get("message"))[:300]
                if detail:
                    parts.append(f"{location}: {detail}" if location else detail)
    return safe_text(" ".join(parts))


def log_activity_problem(
    tx: Transaction, invocation: dict[str, Any], data: dict[str, Any], summary: str
) -> None:
    outcome = get_tool_outcome(tx, invocation["invocation_id"]) or {}
    record = dict(
        event="native_activity_problem",
        occurred_at=now(),
        project_id=tx.project_id,
        **{
            name: invocation.get(name)
            for name in ("work_id", "request_id", "invocation_id", "kind", "name", "state")
        },
        phase=outcome.get("phase") or "native_execution",
        error_code=outcome.get("error_code") or error_fields(data).get("code"),
        exit_code=shell_exit_code(data),
        summary=summary,
    )
    record = {
        name: safe_text(value) if isinstance(value, str) else value
        for name, value in record.items()
    }
    logging.getLogger("proscenium.copilot").warning("%s", json.dumps(record, sort_keys=True))


def failure_summary(tx: Transaction, invocation: dict[str, Any], data: dict[str, Any]) -> str:
    error = error_fields(data)
    if (
        error.get("code") in REJECTIONS
        or "user rejected this tool call" in error.get("message", "").lower()
    ):
        stages = [
            stage
            for stage in tx.all("copilot.permissions")
            if all(
                stage.get(name) == invocation.get(name)
                for name in ("session_id", "work_id", "request_id", "delivery_id")
            )
            and stage.get("tool_call_id") == invocation["native_id"]
            and stage.get("phase") == "completed"
            and stage.get("reply") == "reject"
            and stage.get("delivery_state") == "acknowledged"
        ]
        if len(stages) == 1:
            stage = stages[0]
            reason = safe_text(stage.get("denial_reason"))
            if stage.get("authority") == "automatic_policy":
                return "Blocked automatically by project access policy. " + (
                    reason or "Review the requested paths and use the authorised project tools."
                )
            if stage.get("authority") == "owner":
                return "This tool call was declined by the owner. Review that decision before continuing."
            if stage.get("authority") == "host":
                return "The application held this tool call. " + (
                    reason or "Reconcile the original work before continuing."
                )
        # The SDK calls automatic/host refusals 'user rejected' too. Do not invent an owner decision.
        return "Permission was rejected, but the rejecting authority was not confirmed. Reconcile the original work before continuing."
    outcome = get_tool_outcome(tx, invocation["invocation_id"])
    if outcome and outcome.get("safe_summary"):
        return diagnostic_summary(outcome["safe_summary"], outcome.get("issues"))
    # Legacy ToolResult failures may lack a trusted receipt. Decode only the known
    # failure envelope, never arbitrary result/stdout content or model-written IDs.
    raw_error = data.get("error")
    raw_message = raw_error.get("message") if isinstance(raw_error, dict) else raw_error
    if isinstance(raw_message, str):
        try:
            envelope = json.loads(raw_message)
        except (ValueError, TypeError):
            envelope = None
        if (
            isinstance(envelope, dict)
            and isinstance(envelope.get("error"), str)
            and isinstance(envelope.get("message"), str)
        ):
            details = envelope.get("details")
            return diagnostic_summary(
                envelope["message"], details.get("issues") if isinstance(details, dict) else None
            )
    exit_code = shell_exit_code(data)
    if exit_code is not None and exit_code != 0 and not error.get("message"):
        return f"Command exited with status {exit_code}. Inspect the command arguments and tool availability before retrying."
    message = error.get("message")
    if message:
        return f"{message} ({error['code']})" if error.get("code") else message
    return "The tool reported a failure without an error message. Inspect the original result before retrying."
