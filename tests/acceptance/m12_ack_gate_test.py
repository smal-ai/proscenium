"""Offline real-pipe framing, callback and fail-closed classification checks."""

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest
from copilot._jsonrpc import JsonRpcClient, ProcessExitedError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "native/studio"))
from m12_ack_gate import CallbackPipeGate, accepted_ack_loss


def test_real_hook_reply_admits_before_pending_send_pipe_is_closed(tmp_path):
    marker = tmp_path / "accepted.json"
    peer = r"""
import json,sys
from pathlib import Path
def read():
 h=sys.stdin.buffer.readline()
 if not h:return None
 n=int(h.split(b':')[1]);assert sys.stdin.buffer.readline()==b'\r\n'
 return json.loads(sys.stdin.buffer.read(n))
def write(v):
 b=json.dumps(v).encode(); frame=b'Content-Length: '+str(len(b)).encode()+b'\r\n\r\n'+b
 # Deliberately fragmented framing, through a real stdout pipe.
 for byte in frame:sys.stdout.buffer.write(bytes([byte]));sys.stdout.buffer.flush()
message=read()
write({'jsonrpc':'2.0','id':71,'method':'hooks.invoke','params':{'sessionId':'session','hookType':'userPromptSubmitted','input':{'prompt':'safe reply'}}})
reply=read();assert reply=={'jsonrpc':'2.0','id':71,'result':{'output':{'real_handler':'ran'}}}
Path(sys.argv[1]).write_text(json.dumps({'messageId':'native-accepted','clientTurnId':'turn'}))
write({'jsonrpc':'2.0','id':message['id'],'result':{'messageId':'native-accepted'}})
sys.stdin.buffer.read()
"""
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", peer, str(marker)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    async def run():
        rpc = JsonRpcClient(process)
        rpc.start(asyncio.get_running_loop())
        calls = []

        async def genuine(params):
            calls.append(params)
            return {"output": {"real_handler": "ran"}}

        rpc.set_request_handler("hooks.invoke", genuine)
        gate = CallbackPipeGate(rpc, "session", "turn", "safe reply")
        gate.install()
        send = asyncio.create_task(
            rpc.request("session.send", {"sessionId": "session", "clientTurnId": "turn"})
        )
        for _ in range(200):
            if marker.exists():
                break
            await asyncio.sleep(0.01)
        assert marker.exists() and not send.done() and len(calls) == 1
        native = json.loads(marker.read_text())
        observed = __import__("time").monotonic_ns()
        facts = gate.cut()
        with pytest.raises(ProcessExitedError):
            await asyncio.wait_for(send, 5)
        facts.update(
            native_user_message_id=native["messageId"],
            native_client_turn_id=native["clientTurnId"],
            native_observed_before_cut_ns=observed,
            send_await_pending_before_cut=True,
            sdk_transport_closed=rpc._transport_closed,
        )
        assert accepted_ack_loss(facts)
        await rpc.stop()

    try:
        asyncio.run(run())
    finally:
        process.terminate()
        process.wait(timeout=5)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream:
                stream.close()


@pytest.mark.parametrize(
    "missing",
    ["native_user_message_id", "genuine_reply_written", "send_rpc_pending", "os_descriptor_closed"],
)
def test_no_pass_by_merely_delaying_or_failing_a_local_await(missing):
    facts = dict(
        native_user_message_id="id",
        native_client_turn_id="turn",
        native_observed_before_cut_ns=1,
        cut_ns=2,
        send_await_pending_before_cut=True,
        send_rpc_pending=True,
        hook_matches=1,
        genuine_reply_writes=1,
        genuine_reply_written=True,
        reader_paused=True,
        reader_expired=False,
        os_descriptor_closed=True,
        sdk_transport_closed=True,
    )
    facts.pop(missing)
    assert not accepted_ack_loss(facts)
