"""Real process ownership tests, no model sends or independent application changes."""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from proscenium.copilot.launcher import identity

pytestmark = pytest.mark.native


def test_sigkill_backend_reaps_actual_exec_native_pid(tmp_path):
    if os.environ.get("W01_NATIVE_PROCESS") != "1":
        pytest.skip("Requires explicit bounded own-process allocation")
    # This parent uses the production Native.open and launcher, without sending a model request.
    root = Path("/tmp/opencode") / ("proscenium-w01-death-" + tmp_path.name)
    root.mkdir(exist_ok=True)
    script = r"""
import asyncio,json,sys
from pathlib import Path
from proscenium.configuration import Settings
from proscenium.copilot.native import Native
from proscenium.services import Services
async def main():
 root=Path(sys.argv[1]); project=root/'project'; project.mkdir(exist_ok=True)
 settings=Settings(data_directory=root/'machine',runtime={'worker_model':'gpt-6.1-sol','cli_path':str(Path('node_modules/@github/copilot-linux-x64/copilot').resolve())})
 services=Services.create(settings)
 services.registry.register(dict(schema_version='1.0',project_id='death-proof',repository_id='death-repo',name='Disposable death proof',path=str(project),native_session_id=None,state='inactive',state_revision=0))
 services.registry.activate('death-proof'); services.acquire_project('death-proof')
 native=Native(services)
 try:
  await native.open('death-proof',None,{'on_event':lambda e:None,'on_permission_request':lambda r,i:None})
  (root/'ready').write_text('ready')
  await asyncio.Event().wait()
 finally:
  await native.close();services.close()
asyncio.run(main())
"""
    independent = subprocess.Popen(["/bin/sleep", "90"])
    parent = subprocess.Popen([sys.executable, "-c", script, str(root)])
    record = None
    try:
        for _ in range(600):
            if (root / "ready").exists():
                break
            assert parent.poll() is None, "Owned backend failed before ready"
            time.sleep(0.05)
        else:
            pytest.fail("Bounded direct runtime startup timed out")
        record = json.loads((root / "machine/copilot-runtime/process.json").read_text())
        native_pid, native_start = record["pid"], record["start"]
        assert identity(native_pid) == native_start
        assert Path(f"/proc/{native_pid}/exe").resolve() == Path(record["binary"])
        parent.kill()
        parent.wait(timeout=10)
        for _ in range(100):
            if identity(native_pid) != native_start:
                break
            time.sleep(0.05)
        assert identity(native_pid) != native_start
        assert independent.poll() is None
        (root / "evidence.json").write_text(
            json.dumps(
                {
                    "backend_sigkill": True,
                    "exec_native_pid": native_pid,
                    "native_start_identity": native_start,
                    "native_no_longer_live": True,
                    "independent_owned_sleep_preserved": True,
                    "root_sends": 0,
                },
                indent=2,
            )
        )
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=10)
        if record and identity(record["pid"]) == record["start"]:
            os.kill(record["pid"], signal.SIGKILL)
        independent.terminate()
        independent.wait(timeout=10)
