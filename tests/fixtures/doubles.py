"""Synthetic offline doubles only. These are not native/provider compatibility evidence."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FakeRuntime:
    outcome: str = "acknowledge"
    sent: list[dict[str, Any]] = field(default_factory=list)

    def send(self, body: dict[str, Any]) -> dict[str, Any]:
        self.sent.append(body)
        if self.outcome == "ack_lost":
            raise TimeoutError("Synthetic acknowledgement loss after admission")
        if self.outcome == "failed":
            raise RuntimeError("Synthetic transport failure")
        return {"native_message_id": f"synthetic-{len(self.sent)}"}


@dataclass
class FakeProvider:
    submissions: int = 0
    outcome: str = "running"

    def submit(self, body: dict[str, Any]) -> dict[str, Any]:
        self.submissions += 1
        if self.outcome == "uncertain":
            raise TimeoutError("Synthetic provider ambiguity")
        return {
            "provider_job_id": f"synthetic-provider-{self.submissions}",
            "observed_state": self.outcome,
        }


@dataclass
class FakeWorker:
    finish_during_cancel: bool = True

    def cancellation_observation(self) -> str:
        return "succeeded" if self.finish_during_cancel else "cancelled"
