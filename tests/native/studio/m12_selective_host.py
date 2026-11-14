"""Reuse accepted test-host orchestration; substitute only its test fault strategy.

No production module is changed. All production hooks and SDK-generated hook
replies remain the accepted host's genuine paths. This new root never loads old
M12 state. Only the exact outgoing send response is selectively lost.
"""

import asyncio
import io
import json
import os
import sqlite3
import stat
import sys
import threading
import time
from contextlib import closing
from pathlib import Path
from uuid import uuid4

import m12_ack_host as fixture
from m12_selective_filter import SelectiveResponseLoss, identity, selective_proof


def checkpoint(path, value):
    """Atomic, fsynced, owner-private diagnostic. Contains no raw RPC payload."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    directory = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def inspect_pipe(stdout):
    raw = getattr(stdout, "raw", stdout)
    descriptor = stdout.fileno()
    details = os.fstat(descriptor)
    assert isinstance(raw, io.FileIO) and raw.fileno() == descriptor
    assert not raw.closed and raw.readable() and stat.S_ISFIFO(details.st_mode)
    return {
        "stdout_type": type(stdout).__name__,
        "raw_type": type(raw).__name__,
        "fileno": descriptor,
        "fstat_mode": details.st_mode,
        "pipe_inode": details.st_ino,
        "is_fifo": True,
        "closed": False,
    }


def close_pipe(stdout):
    before = inspect_pipe(stdout)
    raw = getattr(stdout, "raw", stdout)
    raw.close()
    try:
        os.fstat(before["fileno"])
        closed = False
    except OSError:
        closed = True
    assert closed and raw.closed
    return {
        "os_descriptor_closed": closed,
        "raw_file_closed": raw.closed,
        "closed_ns": time.monotonic_ns(),
    }


class ObservedLoss(SelectiveResponseLoss):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.root = Path(sys.argv[1])
        self.journal_lock = threading.RLock()
        self.transport_preflight = inspect_pipe(self.stdout)  # Actual SDK, no close.
        self.persist("00-actual-sdk-transport-preflight")

    def persist(self, stage):
        with self.journal_lock:
            facts = dict(self.facts)
            future = self.rpc.pending_requests.get(self.target_id)
            record = {
                "stage": stage,
                "observed_ns": time.monotonic_ns(),
                "request_id": self.client_turn_id,
                "session_id": self.session_id,
                "send_rpc_id": self.target_id,
                "rpc_future_pending": future is not None and not future.done(),
                "transport_preflight": self.transport_preflight,
                "facts": facts,
            }
            checkpoint(self.root / "checkpoints" / f"{stage}.json", record)
            checkpoint(self.root / "checkpoint-latest-private.json", record)
            return record

    def dispatch(self, message):
        selected = (
            self.target_id is not None
            and message.get("id") == self.target_id
            and "method" not in message
        )
        result = super().dispatch(message)  # Exact accepted response-selective logic unchanged.
        if selected:
            self.persist("01-target-response-withheld")  # BEFORE returning to the read loop.
        return result

    async def write(self, message, **kwargs):
        result = await super().write(message, **kwargs)  # Every genuine stdin reply unaltered.
        if message.get("id") in self.hook_ids and "method" not in message:
            self.persist("02-genuine-hook-reply-forwarded")
        return result

    def cut(self):
        root = self.root
        matches = []
        path = root / "d/copilot-runtime/home/session-state" / self.session_id / "events.jsonl"
        for line in path.open():
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if value.get("type") != "user.message":
                continue
            data = value.get("data", {})
            if (
                data.get("clientTurnId") == self.client_turn_id
                and data.get("content") == self.prompt
            ):
                matches.append(
                    {
                        "native_user_message_id": data.get("messageId"),
                        "native_client_turn_id": data["clientTurnId"],
                        "native_event_id": value["id"],
                        "native_timestamp": value.get("timestamp"),
                    }
                )
        if len(matches) != 1:
            self.persist("03-native-acceptance-not-unique")
            raise RuntimeError("No unique native acceptance")
        self.facts.update(matches[0])
        self.facts["native_observed_before_cut_ns"] = time.monotonic_ns()
        self.facts["send_await_pending_before_cut"] = any(
            t.get_name() == f"copilot-send:{self.client_turn_id}" and not t.done()
            for t in asyncio.all_tasks()
        )
        with closing(
            sqlite3.connect((root / "p/.proscenium/state.sqlite3").as_uri() + "?mode=ro", uri=True)
        ) as db:
            row = db.execute(
                "SELECT body FROM records WHERE namespace='requests' AND id=?",
                (self.client_turn_id,),
            ).fetchone()
            self.facts["application_receipt_before_pipe_close"] = (
                json.loads(row[0])["receipt"] if row else None
            )
        self.facts["send_rpc_pending"] = self.target_id in self.rpc.pending_requests
        self.persist("03-native-acceptance-before-cut")
        assert self.facts["send_await_pending_before_cut"] and self.facts["send_rpc_pending"]
        self.facts.update(
            cut_ns=time.monotonic_ns(),
            pending_rpc_count=len(self.rpc.pending_requests),
            real_stdout_is_pipe=True,
        )
        self.persist("04-cut-intent")
        self.facts.update(close_pipe(self.stdout))
        self.persist("05-real-descriptor-closed")  # BEFORE any writer fencing or await.
        process = self.rpc.process
        if process.poll() is None:
            assert identity(process.pid) == self.process_start
            self.facts.update(
                owned_writer_pid=process.pid,
                owned_writer_start=self.process_start,
                owned_writer_fence_ns=time.monotonic_ns(),
            )
            self.persist("06-owned-writer-fence-intent")
            process.terminate()
        self.persist("07-owned-writer-fence-sent")
        return dict(self.facts)


def witnessed_proof(facts):
    root = Path(sys.argv[1])
    checkpoint(root / "checkpoints/08-sdk-transport-result.json", facts)
    return selective_proof(facts)


if __name__ == "__main__":
    fixture.CallbackPipeGate = ObservedLoss
    fixture.accepted_ack_loss = witnessed_proof
    fixture.main()
