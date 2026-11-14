"""Injected loss of one actual parsed RPC response, then actual pipe closure.

No production hooks are replaced. No other messages are filtered. Native auth
traffic is not inspected. This is intentionally NOT spontaneous network loss.
"""

import hashlib
import json
import os
import stat
import threading
import time
from pathlib import Path


def identity(pid):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
        return None if fields[0] == "Z" else fields[19]
    except FileNotFoundError:
        return None


class SelectiveResponseLoss:
    def __init__(self, rpc, session_id, client_turn_id, prompt):
        self.rpc, self.session_id = rpc, session_id
        self.client_turn_id, self.prompt = client_turn_id, prompt
        self.original_dispatch = rpc._handle_message
        self.original_write = rpc._send_message
        self.target_id = None
        self.hook_ids = set()
        self.reply_written = threading.Event()
        self.release = threading.Event()  # Existing fixture cleanup interface, never gates reads.
        self.stdout = rpc.process.stdout
        self.descriptor = self.stdout.fileno()
        assert stat.S_ISFIFO(os.fstat(self.descriptor).st_mode)
        self.process_start = identity(rpc.process.pid)
        self.facts = {
            "withheld_response_count": 0,
            "other_responses_forwarded": 0,
            "events_forwarded": 0,
            "server_requests_forwarded": 0,
            "hook_matches": 0,
            "genuine_reply_writes": 0,
            "fault_type": "injected_selective_rpc_response_loss",
        }

    def install(self):
        self.rpc._handle_message = self.dispatch
        self.rpc._send_message = self.write

    def dispatch(self, message):
        response = "id" in message and "method" not in message
        if response and self.target_id is not None and message["id"] == self.target_id:
            self.facts["withheld_response_count"] += 1
            data = json.dumps(message, sort_keys=True, separators=(",", ":")).encode()
            self.facts.update(
                withheld_response_sha256=hashlib.sha256(data).hexdigest(),
                withheld_response_ns=time.monotonic_ns(),
                withheld_native_message_id=message.get("result", {}).get("messageId"),
                withheld_response_is_error="error" in message,
            )
            # Only this actual response is dropped BEFORE the original dispatcher
            # can complete its pending future or call on_response_inline.
            return
        if response:
            self.facts["other_responses_forwarded"] += 1
        elif "method" in message and "id" in message:
            self.facts["server_requests_forwarded"] += 1
            if message["method"] == "hooks.invoke":
                params = message.get("params", {})
                if (
                    params.get("sessionId") == self.session_id
                    and params.get("hookType") == "userPromptSubmitted"
                ):
                    if params.get("input", {}).get("prompt") == self.prompt:
                        self.hook_ids.add(message["id"])
                        self.facts.update(
                            hook_matches=self.facts["hook_matches"] + 1,
                            hook_rpc_id=message["id"],
                            hook_received_ns=time.monotonic_ns(),
                        )
        elif "method" in message:
            self.facts["events_forwarded"] += 1
        return self.original_dispatch(message)  # Same object, unmodified, all non-target traffic.

    async def write(self, message, **kwargs):
        if message.get("method") == "session.send":
            params = message.get("params", {})
            if (
                params.get("sessionId") == self.session_id
                and params.get("clientTurnId") == self.client_turn_id
            ):
                if self.target_id is not None:
                    raise RuntimeError("Only one actual send is allowed")
                self.target_id = message["id"]
                self.facts.update(
                    send_rpc_id=self.target_id, send_write_started_ns=time.monotonic_ns()
                )
        reply = "method" not in message and message.get("id") in self.hook_ids
        if reply:
            before = json.dumps(message, sort_keys=True, separators=(",", ":")).encode()
            await self.original_write(message, **kwargs)
            assert before == json.dumps(message, sort_keys=True, separators=(",", ":")).encode()
            self.facts.update(
                genuine_reply_writes=self.facts["genuine_reply_writes"] + 1,
                genuine_reply_sha256=hashlib.sha256(before).hexdigest(),
                genuine_reply_written_ns=time.monotonic_ns(),
                hook_reply_error="error" in message,
            )
            self.reply_written.set()
            return
        return await self.original_write(message, **kwargs)

    def cut(self):
        self.facts.update(
            cut_ns=time.monotonic_ns(),
            send_rpc_pending=self.target_id in self.rpc.pending_requests,
            pending_rpc_count=len(self.rpc.pending_requests),
            real_stdout_is_pipe=True,
        )
        # BufferedReader.close can deadlock against a blocking reader thread.
        # FileIO.close closes the REAL FD without that buffered-reader lock and
        # marks raw.closed, avoiding later descriptor reuse/double-close hazards.
        self.stdout.raw.close()
        try:
            os.fstat(self.descriptor)
            self.facts["os_descriptor_closed"] = False
        except OSError:
            self.facts["os_descriptor_closed"] = True
        self.facts["raw_file_closed"] = self.stdout.raw.closed
        # Linux may keep a blocked read syscall alive after close(fd). Fence the
        # exact owned writer so real pipe EOF unblocks it. Never fabricate SDK EOF.
        process = self.rpc.process
        if process.poll() is None:
            if identity(process.pid) != self.process_start:
                raise RuntimeError("Owned writer identity changed")
            self.facts.update(
                owned_writer_pid=process.pid,
                owned_writer_start=self.process_start,
                owned_writer_fence_ns=time.monotonic_ns(),
            )
            process.terminate()
        return dict(self.facts)


def selective_proof(facts):
    return bool(
        facts.get("native_user_message_id")
        and facts.get("native_user_message_id") == facts.get("withheld_native_message_id")
        and facts.get("native_observed_before_cut_ns", 2**64) < facts.get("cut_ns", 0)
        and facts.get("withheld_response_count") == 1
        and not facts.get("withheld_response_is_error")
        and facts.get("hook_matches") == 1
        and facts.get("genuine_reply_writes") == 1
        and facts.get("send_await_pending_before_cut")
        and facts.get("send_rpc_pending")
        and not facts.get("application_send_ack_delivered")
        and facts.get("os_descriptor_closed")
        and facts.get("raw_file_closed")
        and facts.get("sdk_transport_closed")
    )
