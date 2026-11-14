"""Production app/lifespan plus private operator-fixture mailbox. No extra HTTP API."""

import hashlib
import importlib
import json
import sys
import threading
from pathlib import Path
from urllib.parse import urlsplit

import m02_fixture
import uvicorn
from proscenium.app import create_app
from proscenium.configuration import Settings
from proscenium.services import Services

root = Path(sys.argv[1])
services = Services.create(Settings.load(root / "config.json"))
app = create_app(services.settings, services=services, manage_features=True)
if not (root / "pairing.url").exists():
    (root / "pairing.url").write_text(
        f"{services.settings.origin}/#pair={services.auth.issue_pairing()}"
    )
stop = threading.Event()


def mailbox():
    while not stop.wait(0.1):
        for path in sorted((root / "commands").glob("*.json")):
            receipt = root / "receipts" / path.name
            claim = path.with_suffix(".claimed")
            if receipt.exists() or claim.exists():
                continue
            path.rename(claim)  # A crash/unknown effect cannot be blindly redispatched.
            command = json.loads(claim.read_text())
            try:
                result = importlib.reload(m02_fixture).apply(services, root, command)
                receipt.write_text(
                    json.dumps(
                        {
                            "status": "completed",
                            "command": command,
                            "fixture_source_sha256": hashlib.sha256(
                                Path(m02_fixture.__file__).read_bytes()
                            ).hexdigest(),
                            "result": result,
                        },
                        indent=2,
                    )
                )
            except Exception as error:
                receipt.write_text(
                    json.dumps(
                        {
                            "status": "failed",
                            "command": command,
                            "error_type": type(error).__name__,
                            "code": getattr(error, "code", None),
                            "message": str(error),
                        },
                        indent=2,
                    )
                )


thread = threading.Thread(target=mailbox, name="m02-operator-fixture", daemon=True)
thread.start()
url = urlsplit(services.settings.origin)
try:
    uvicorn.run(app, host=url.hostname, port=url.port, workers=1)
finally:
    stop.set()
    thread.join()
    services.close()
