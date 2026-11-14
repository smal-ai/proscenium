"""Offline SDK wire/evidence checks. These do not execute or prove a model."""

import inspect
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

from copilot import CopilotClient
from copilot.client import RuntimeConnection
from copilot.generated.rpc import (
    QueueSetDrainPausedRequest,
    QueueWithdrawMessageRequest,
    SandboxConfig,
)
from copilot.session import CopilotSession

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools/proofs/copilot"))

from live import CloudBudget  # noqa: E402
from probe import clean_environment  # noqa: E402
from replay_attribution import replay  # noqa: E402


class FakeRpc:
    def __init__(self):
        self.calls = []

    async def request(self, method, params, **kwargs):
        self.calls.append((method, params))
        if method in {"session.create", "session.resume"}:
            response = {"sessionId": params["sessionId"], "workspacePath": None}
            if callback := kwargs.get("on_response_inline"):
                callback(response)
            return response
        return {"messageId": "native-message"}


class WireTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_preserves_human_prompt_and_context(self):
        rpc = FakeRpc()
        session = CopilotSession("owned-session", rpc)
        prompt = "Render this view."
        attachment = {
            "type": "extension_context",
            "extensionId": "proscenium",
            "title": "Turn context",
            "capturedAt": "2026-11-14T12:00:00+02:00",
            "payload": {"context_id": "context-1"},
        }
        ack = await session.send(
            prompt,
            display_prompt=prompt,
            client_turn_id="request-1",
            attachments=[attachment],
            mode="immediate",
        )
        method, payload = rpc.calls[-1]
        self.assertEqual(method, "session.send")
        self.assertEqual(ack, "native-message")
        self.assertEqual(payload["prompt"], prompt)
        self.assertEqual(payload["displayPrompt"], prompt)
        self.assertEqual(payload["clientTurnId"], "request-1")
        self.assertEqual(payload["attachments"], [attachment])
        self.assertNotIn("source", payload)

    async def test_create_resume_send_both_separate_memory_controls(self):
        client = CopilotClient(connection=RuntimeConnection.for_stdio(path=sys.executable), env={})
        rpc = FakeRpc()
        client._client = rpc
        settings = {
            "working_directory": "/synthetic/project",
            "memory": {"enabled": False},
            "enable_session_store": False,
            "sandbox_config": SandboxConfig(enabled=False),
        }
        await client.create_session(session_id="created", **settings)
        await client.resume_session("resumed", **settings, continue_pending_work=False)
        for method, payload in rpc.calls:
            if method in {"session.create", "session.resume"}:
                self.assertEqual(payload["workingDirectory"], "/synthetic/project")
                self.assertEqual(payload["memory"], {"enabled": False})
                self.assertIs(payload["enableSessionStore"], False)
                self.assertEqual(payload["sandboxConfig"]["enabled"], False)
                if method == "session.resume":
                    self.assertIs(payload["continuePendingWork"], False)

    async def test_native_pause_and_withdraw_use_distinct_message_identity(self):
        rpc = FakeRpc()
        session = CopilotSession("owned-session", rpc)
        await session.rpc.queue.set_drain_paused(QueueSetDrainPausedRequest(paused=True))
        self.assertEqual(
            rpc.calls[-1],
            (
                "session.queue.setDrainPaused",
                {"sessionId": "owned-session", "paused": True},
            ),
        )
        # Test serialization without pretending this fake proves native removal.
        params = QueueWithdrawMessageRequest(
            expected_prompt="unchanged", message_id="native-message"
        )
        self.assertEqual(
            params.to_dict(),
            {"expectedPrompt": "unchanged", "messageId": "native-message"},
        )


class SafetyTests(unittest.TestCase):
    def test_budget_is_durable_and_not_refunded_after_uncertain_send(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "budget.jsonl"
            for number in range(8):
                CloudBudget(path).reserve(f"request-{number}")
            with self.assertRaises(RuntimeError):
                CloudBudget(path).reserve("retry-is-not-free")
            self.assertEqual(len(path.read_text().splitlines()), 8)

    def test_no_environment_credentials_are_forwarded(self):
        env = clean_environment(Path("/synthetic/runtime"))
        self.assertFalse(any("TOKEN" in key or "API_KEY" in key for key in env))
        self.assertEqual(env["COPILOT_DISABLE_LOGIN_SHELL_ENV"], "1")

    def test_accidental_rerun_does_not_repeat_a_reserved_send(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "budget.jsonl"
            CloudBudget(path).reserve("same-send")
            with self.assertRaises(RuntimeError):
                CloudBudget(path).reserve("same-send")
            self.assertEqual(len(path.read_text().splitlines()), 1)

    def test_resume_exposes_noncontinuing_control(self):
        self.assertIn(
            "continue_pending_work",
            inspect.signature(CopilotClient.resume_session).parameters,
        )


class CapturedEvidenceTests(unittest.TestCase):
    def load(self, name):
        return json.loads((ROOT / "docs/evidence/e01" / name).read_text())

    def test_real_hook_context_reaches_root_and_native_child(self):
        result = self.load("advanced.json")["advanced"]
        reports = result["reports"]
        self.assertEqual(len(reports), 2)
        self.assertIsNone(reports[0]["agent_id"])
        self.assertIsNotNone(reports[1]["agent_id"])
        self.assertTrue(
            all(report["arguments"]["sentinel"] == result["context_sentinel"] for report in reports)
        )
        self.assertEqual(
            result["prompt_hooks"][1]["workingDirectory"],
            result["prompt_hooks"][0]["workingDirectory"],
        )

    def test_sqlite_replay_joins_callbacks_and_rejects_reused_event_ids(self):
        capture = self.load("advanced.json")["advanced"]
        with sqlite3.connect(":memory:") as connection:
            callbacks = replay(connection, capture)
            before = connection.execute("SELECT count(*) FROM events").fetchone()[0]
            self.assertEqual(replay(connection, capture), callbacks)
            self.assertEqual(
                connection.execute("SELECT count(*) FROM events").fetchone()[0], before
            )
            self.assertTrue(all(row["request_id"] == "e01-hook-child" for row in callbacks))
            changed = json.loads(json.dumps(capture))
            changed["events"][0]["data"]["content"] = "different body"
            with self.assertRaises(ValueError):
                replay(connection, changed)

    def test_real_deferred_steer_barrier(self):
        result = self.load("advanced.json")["advanced"]
        self.assertTrue(result["pause_acknowledged_before_race"])
        self.assertTrue(result["native_idle_before_stop"])
        self.assertTrue(result["abort_acknowledged"])
        self.assertEqual(
            result["deferred_queue"]["items"][0]["messageId"],
            result["late_application_ack"]["message_id"],
        )
        self.assertEqual(result["queue_after_stop"]["items"], [])
        self.assertEqual(result["queue_after_stop"]["steeringMessages"], [])
        self.assertEqual(result["after_barrier_events"], [])
        self.assertEqual(result["cold_resume_queue"]["items"], [])
        self.assertFalse(result["pause_released"])
        self.assertFalse(
            any(
                event["type"] in {"user.message", "assistant.message", "tool.execution_start"}
                for event in result["cold_resume_events"]
            )
        )

    def test_real_two_project_resumes_did_not_reveal_other_sentinel(self):
        reports = self.load("followup.json")["followup"]["reports"]
        self.assertEqual(len(reports), 2)
        self.assertTrue(reports[0]["arguments"]["context_sentinel"].startswith("E01_B_"))
        self.assertTrue(reports[1]["arguments"]["context_sentinel"].startswith("E01_A_"))
        self.assertTrue(
            all(report["arguments"]["other_project_sentinel"] == "unknown" for report in reports)
        )

    def test_direct_runtime_is_reaped_on_graceful_and_forced_exit(self):
        observations = self.load("lifecycle.json")["lifecycle"]["observations"]
        self.assertEqual([row["action"] for row in observations], ["stop", "force_stop"])
        self.assertTrue(
            all(
                row["poll_after"] is not None and not row["proc_exists_after"]
                for row in observations
            )
        )

    def test_unavailable_required_worker_never_started(self):
        result = self.load("shell-and-model-result.json")["required_model"]
        self.assertFalse(result["success"])
        self.assertEqual(result["telemetry_error"], "invalid_model")
        self.assertEqual(result["subagent_started_events"], 0)

    def test_unhandled_first_message_crash_is_not_relabelled_a_successful_load(self):
        result = self.load("lifecycle.json")["lifecycle"]["pending_cold_load"]
        self.assertEqual(result["resume_status"], "native_session_missing")
        self.assertEqual(result["prompt_attempts_blocked_by_guard"], [])
        self.assertEqual(result["cloud_model_requests"], 0)
        self.assertEqual(
            result["pending_before_forced_exit"]["items"][0]["messageId"],
            result["native_message_id"],
        )


if __name__ == "__main__":
    unittest.main()
