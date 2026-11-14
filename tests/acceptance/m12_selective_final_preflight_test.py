"""Full helper preflight before model allocation: actual pipes, persistence, EOF."""

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from copilot._jsonrpc import JsonRpcClient, ProcessExitedError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "native/studio"))
from m12_selective_host import ObservedLoss, checkpoint


@pytest.mark.parametrize("buffer_size", [-1, 0])
def test_complete_helper_on_actual_buffered_and_fileio_pipes(tmp_path, monkeypatch, buffer_size):
    monkeypatch.setattr(sys, "argv", ["host", str(tmp_path), "1"])
    native = tmp_path / "d/copilot-runtime/home/session-state/session/events.jsonl"
    native.parent.mkdir(parents=True)
    database = tmp_path / "p/.proscenium/state.sqlite3"
    database.parent.mkdir(parents=True)
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE records(namespace TEXT,id TEXT,body TEXT)")
        db.execute(
            "INSERT INTO records VALUES(?,?,?)",
            (
                "requests",
                "turn",
                json.dumps({"receipt": {"state": "dispatching", "native_message_id": None}}),
            ),
        )
    peer = r"""
import json,sys
from pathlib import Path
def read():
 h=sys.stdin.buffer.readline()
 if not h:return None
 n=int(h.split(b':')[1]);assert sys.stdin.buffer.readline()==b'\r\n'
 return json.loads(sys.stdin.buffer.read(n))
def write(value):
 b=json.dumps(value).encode();frame=b'Content-Length: '+str(len(b)).encode()+b'\r\n\r\n'+b
 for byte in frame:sys.stdout.buffer.write(bytes([byte]));sys.stdout.buffer.flush()
send=read()
write({'jsonrpc':'2.0','id':send['id'],'result':{'messageId':'native-id'}})
write({'jsonrpc':'2.0','id':71,'method':'hooks.invoke','params':{'sessionId':'session','hookType':'userPromptSubmitted','input':{'prompt':'safe'}}})
reply=read();assert reply=={'jsonrpc':'2.0','id':71,'result':{'output':{'genuine':True}}}
Path(sys.argv[1]).write_text(json.dumps({'id':'event-id','type':'user.message','timestamp':'2026-11-14T00:00:00Z','data':{'messageId':'native-id','clientTurnId':'turn','content':'safe'}})+'\n')
sys.stdin.buffer.read()
"""
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", peer, str(native)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=buffer_size,
    )

    async def run():
        rpc = JsonRpcClient(process)
        rpc.start(asyncio.get_running_loop())

        async def hook(_params):
            return {"output": {"genuine": True}}

        rpc.set_request_handler("hooks.invoke", hook)
        loss = ObservedLoss(rpc, "session", "turn", "safe")
        loss.install()
        assert loss.transport_preflight["stdout_type"] == (
            "FileIO" if buffer_size == 0 else "BufferedReader"
        )
        send = asyncio.create_task(
            rpc.request("session.send", {"sessionId": "session", "clientTurnId": "turn"}),
            name="copilot-send:turn",
        )
        for _ in range(200):
            if native.exists() and loss.reply_written.is_set():
                break
            await asyncio.sleep(0.01)
        assert native.exists() and not send.done()
        before = json.loads((tmp_path / "checkpoints/01-target-response-withheld.json").read_text())
        assert before["rpc_future_pending"] and before["facts"]["withheld_response_count"] == 1
        assert before["request_id"] == "turn" and before["session_id"] == "session"
        assert before["send_rpc_id"] and before["facts"]["withheld_response_sha256"]
        facts = loss.cut()
        assert (tmp_path / "checkpoints/03-native-acceptance-before-cut.json").exists()
        assert (tmp_path / "checkpoints/05-real-descriptor-closed.json").exists()
        assert facts["os_descriptor_closed"] and facts["native_user_message_id"] == "native-id"
        with pytest.raises(ProcessExitedError):
            await asyncio.wait_for(send, 5)
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


def test_standalone_import_and_atomic_output_paths(tmp_path):
    scripts = Path(__file__).resolve().parents[1] / "native/studio"
    subprocess.run(
        [sys.executable, "-c", "import m12_selective_host"],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(scripts)},
        capture_output=True,
        check=True,
        timeout=10,
    )
    path = tmp_path / "checkpoints/probe.json"
    checkpoint(path, {"stage": "before"})
    checkpoint(path, {"stage": "after"})
    assert json.loads(path.read_text()) == {"stage": "after"}
    assert path.stat().st_mode & 0o777 == 0o600
    assert not list(path.parent.glob("*.tmp"))
