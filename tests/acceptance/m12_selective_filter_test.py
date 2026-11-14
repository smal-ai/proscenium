"""Offline exact observed ordering: ACK, real hook/reply, acceptance, real EOF."""

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from copilot._jsonrpc import JsonRpcClient, ProcessExitedError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "native/studio"))
from m12_selective_filter import SelectiveResponseLoss, selective_proof


def test_standalone_selective_host_import_does_not_start_sdk(tmp_path):
    script = Path(__file__).resolve().parents[1] / "native/studio"
    subprocess.run(
        [sys.executable, "-c", 'import m12_selective_host; print("import-only")'],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(script)},
        capture_output=True,
        check=True,
        timeout=10,
    )


@pytest.mark.parametrize("buffer_size", [-1, 0])
def test_ack_before_hook_is_withheld_while_all_other_traffic_flows(tmp_path, buffer_size):
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
 b=json.dumps(v).encode();frame=b'Content-Length: '+str(len(b)).encode()+b'\r\n\r\n'+b
 for byte in frame:sys.stdout.buffer.write(bytes([byte]));sys.stdout.buffer.flush()
send=read()
# The actual observed native order. ACK precedes hooks.invoke.
write({'jsonrpc':'2.0','id':send['id'],'result':{'messageId':'accepted-id'}})
write({'jsonrpc':'2.0','id':71,'method':'hooks.invoke','params':{'sessionId':'session','hookType':'userPromptSubmitted','input':{'prompt':'safe'}}})
reply=read();assert reply=={'jsonrpc':'2.0','id':71,'result':{'output':{'real':'reply'}}}
Path(sys.argv[1]).write_text(json.dumps({'messageId':'accepted-id','clientTurnId':'turn'}))
write({'jsonrpc':'2.0','method':'session.event','params':{'safe':True}})
other=read();write({'jsonrpc':'2.0','id':other['id'],'result':{'ping':'ok'}})
sys.stdin.buffer.read()
"""
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", peer, str(marker)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=buffer_size,
    )

    async def run():
        rpc = JsonRpcClient(process)
        rpc.start(asyncio.get_running_loop())
        calls = []
        events = []
        inline = []

        async def hook(params):
            calls.append(params)
            return {"output": {"real": "reply"}}

        rpc.set_request_handler("hooks.invoke", hook)
        rpc.set_notification_handler(lambda method, params: events.append(method))
        loss = SelectiveResponseLoss(rpc, "session", "turn", "safe")
        loss.install()
        send = asyncio.create_task(
            rpc.request(
                "session.send",
                {"sessionId": "session", "clientTurnId": "turn"},
                on_response_inline=lambda _v: inline.append(True),
            )
        )
        for _ in range(200):
            if marker.exists():
                break
            await asyncio.sleep(0.01)
        assert marker.exists() and not send.done() and not inline and len(calls) == 1
        assert (
            loss.facts["withheld_response_ns"]
            < loss.facts["hook_received_ns"]
            < loss.facts["genuine_reply_written_ns"]
        )
        assert await rpc.request("ping", timeout=5) == {"ping": "ok"}
        assert events == ["session.event"]
        native = json.loads(marker.read_text())
        observed = time.monotonic_ns()
        facts = loss.cut()
        with pytest.raises(ProcessExitedError):
            await asyncio.wait_for(send, 5)
        facts.update(
            native_user_message_id=native["messageId"],
            native_observed_before_cut_ns=observed,
            send_await_pending_before_cut=True,
            application_send_ack_delivered=False,
            sdk_transport_closed=rpc._transport_closed,
        )
        assert selective_proof(facts)
        assert loss.facts["other_responses_forwarded"] == 1
        assert not inline
        await rpc.stop()

    try:
        asyncio.run(run())
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=5)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream:
                stream.close()


@pytest.mark.parametrize(
    "bad", ["application_send_ack_delivered", "withheld_response_count", "native_user_message_id"]
)
def test_no_pass_without_exact_acceptance_pending_ack_and_single_drop(bad):
    facts = dict(
        native_user_message_id="id",
        withheld_native_message_id="id",
        native_observed_before_cut_ns=1,
        cut_ns=2,
        withheld_response_count=1,
        hook_matches=1,
        genuine_reply_writes=1,
        send_await_pending_before_cut=True,
        send_rpc_pending=True,
        application_send_ack_delivered=False,
        os_descriptor_closed=True,
        raw_file_closed=True,
        sdk_transport_closed=True,
    )
    facts[bad] = {
        "application_send_ack_delivered": True,
        "withheld_response_count": 2,
        "native_user_message_id": None,
    }[bad]
    assert not selective_proof(facts)
