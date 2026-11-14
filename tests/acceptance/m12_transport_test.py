"""Offline real-pipe fault checks. No vendor runtime, cloud, native editor or GPU."""

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from copilot._jsonrpc import JsonRpcClient, ProcessExitedError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "native/studio"))
from m12_host import SID, close_actual_stdout, pause_actual_stdout, reserve_send


def test_send_budget_is_durable_and_never_retries_an_unknown_send(tmp_path):
    plan = {"request_ids": ["one", "two", "third"]}
    reserve_send(tmp_path, plan, "one", SID, "Readonly first request")
    with pytest.raises(RuntimeError, match="duplicate"):
        reserve_send(tmp_path, plan, "one", SID, "Same unknown send must not replay")
    reserve_send(tmp_path, plan, "two", SID, "Readonly second request")
    with pytest.raises(RuntimeError, match="exhausted"):
        reserve_send(tmp_path, plan, "third", SID, "Forbidden third request")
    assert [r["request_id"] for r in json.loads((tmp_path / "sends-private.json").read_text())] == [
        "one",
        "two",
    ]


def test_real_sdk_jsonrpc_pending_request_fails_only_after_actual_pipe_close(tmp_path):
    marker = tmp_path / "admitted"
    peer = r"""
import json,sys
from pathlib import Path
while True:
    header=sys.stdin.buffer.readline()
    if not header: break
    count=int(header.split(b':')[1])
    assert sys.stdin.buffer.readline()==b'\r\n'
    message=json.loads(sys.stdin.buffer.read(count))
    if message['method']=='send': Path(sys.argv[1]).write_text('actual stdin request received')
    body=json.dumps({'jsonrpc':'2.0','id':message['id'],'result':{'ok':True}}).encode()
    sys.stdout.buffer.write(b'Content-Length: '+str(len(body)).encode()+b'\r\n\r\n'+body)
    sys.stdout.buffer.flush()
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
        client = SimpleNamespace(_client=rpc, _process=process)
        loss = await pause_actual_stdout(client)
        pending = asyncio.create_task(rpc.request("send", {"readonly": True}))
        for _ in range(100):
            if marker.exists():
                break
            await asyncio.sleep(0.01)
        assert marker.exists() and not pending.done()
        facts = close_actual_stdout(loss)
        assert facts["actual_stdout_fd_closed"] and not facts["reader_pause_expired"]
        with pytest.raises(ProcessExitedError):
            await asyncio.wait_for(pending, 5)
        assert rpc._transport_closed
        await rpc.stop()

    try:
        asyncio.run(run())
    finally:
        process.terminate()
        process.wait(timeout=5)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream:
                stream.close()
