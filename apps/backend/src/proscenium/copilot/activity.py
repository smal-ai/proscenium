"""Bounded owner-visible failure diagnostics, never raw tool output or reasoning."""

import re
from typing import Any

from proscenium.db import Transaction

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
    message = error.get("message")
    if message:
        return f"{message} ({error['code']})" if error.get("code") else message
    return "The tool reported a failure without an error message. Inspect the original result before retrying."
