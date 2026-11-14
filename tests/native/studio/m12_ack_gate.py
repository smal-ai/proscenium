"""One callback-aware actual pipe fault. Pinned SDK test instrumentation only.

No hook implementation/reply is replaced. No response is fabricated or suppressed.
Only the selected public-input hooks.invoke ID and its genuine reply are observed.
"""

import asyncio
import hashlib
import json
import os
import stat
import threading
import time


class CallbackPipeGate:
    def __init__(self, rpc, session_id, client_turn_id, prompt):
        self.rpc = rpc
        self.session_id = session_id
        self.client_turn_id = client_turn_id
        self.prompt = prompt
        self.hook_id = None
        self.pause = threading.Event()
        self.paused = threading.Event()
        self.release = threading.Event()
        self.expired = threading.Event()
        self.reply_written = threading.Event()
        self.facts = {"hook_matches": 0, "genuine_reply_writes": 0}
        self.original_request = rpc._handle_request
        self.original_read = rpc._read_message
        self.original_write = rpc._send_message
        self.stdout = rpc.process.stdout
        self.descriptor = self.stdout.fileno()
        assert stat.S_ISFIFO(os.fstat(self.descriptor).st_mode)

    def install(self):
        self.rpc._handle_request = self.handle_request
        self.rpc._read_message = self.read_message
        self.rpc._send_message = self.send_message

    def handle_request(self, message):
        # Inspect no auth methods or unrelated hook payloads. These comparisons
        # are only to our explicitly armed public prompt and current session.
        if message.get("method") == "hooks.invoke":
            params = message.get("params", {})
            if (
                params.get("sessionId") == self.session_id
                and params.get("hookType") == "userPromptSubmitted"
            ):
                if params.get("input", {}).get("prompt") == self.prompt:
                    self.facts["hook_matches"] += 1
                    if self.hook_id is not None:
                        raise RuntimeError("More than one selected hook")
                    self.hook_id = message["id"]
                    self.facts.update(
                        hook_rpc_id=self.hook_id, hook_received_ns=time.monotonic_ns()
                    )
                    # The next actual read pauses, but normal async dispatch and
                    # the real production hook still run on the application loop.
                    self.pause.set()
        return self.original_request(message)

    def read_message(self):
        if self.pause.is_set():
            self.paused.set()
            if not self.release.wait(25):
                self.expired.set()  # Read normally on expiry. Never inject an exception.
        return self.original_read()

    async def send_message(self, message, **kwargs):
        if message.get("method") == "session.send":
            params = message.get("params", {})
            if (
                params.get("sessionId") == self.session_id
                and params.get("clientTurnId") == self.client_turn_id
            ):
                self.facts["send_rpc_id"] = message["id"]
                self.facts["send_write_started_ns"] = time.monotonic_ns()
        genuine = (
            self.hook_id is not None
            and message.get("id") == self.hook_id
            and "method" not in message
        )
        if genuine:
            if "error" in message or "result" not in message:
                self.facts["hook_reply_error"] = True
                return await self.original_write(message, **kwargs)
            assert await asyncio.to_thread(self.paused.wait, 5), (
                "Reader never reached pipe boundary"
            )
            before = json.dumps(message, sort_keys=True, separators=(",", ":")).encode()
            await self.original_write(message, **kwargs)  # Actual SDK stdin write and flush.
            assert before == json.dumps(message, sort_keys=True, separators=(",", ":")).encode()
            self.facts.update(
                genuine_reply_writes=self.facts["genuine_reply_writes"] + 1,
                genuine_reply_sha256=hashlib.sha256(before).hexdigest(),
                genuine_reply_written_ns=time.monotonic_ns(),
            )
            self.reply_written.set()
            return
        return await self.original_write(message, **kwargs)

    def cut(self):
        self.facts.update(
            cut_ns=time.monotonic_ns(),
            reader_paused=self.paused.is_set(),
            reader_expired=self.expired.is_set(),
            genuine_reply_written=self.reply_written.is_set(),
            send_rpc_pending=self.facts.get("send_rpc_id") in self.rpc.pending_requests,
            pending_rpc_count=len(self.rpc.pending_requests),
            real_stdout_is_pipe=True,
        )
        self.stdout.close()  # Real OS read descriptor. No SDK-local error fabrication.
        try:
            os.fstat(self.descriptor)
            self.facts["os_descriptor_closed"] = False
        except OSError:
            self.facts["os_descriptor_closed"] = True
        self.release.set()
        return dict(self.facts)


def accepted_ack_loss(facts):
    return bool(
        facts.get("native_user_message_id")
        and facts.get("native_client_turn_id")
        and facts.get("native_observed_before_cut_ns", 2**64) < facts.get("cut_ns", 0)
        and facts.get("send_await_pending_before_cut")
        and facts.get("send_rpc_pending")
        and facts.get("hook_matches") == 1
        and facts.get("genuine_reply_writes") == 1
        and facts.get("genuine_reply_written")
        and facts.get("reader_paused")
        and not facts.get("reader_expired")
        and facts.get("os_descriptor_closed")
        and facts.get("sdk_transport_closed")
        and not facts.get("application_send_ack_delivered")
    )
