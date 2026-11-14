"""Read only the explicitly owned M01 bridge, never the default desktop port."""

import hashlib
import json
import socket
import sys
from datetime import UTC, datetime
from pathlib import Path

root, label = Path(sys.argv[1]), sys.argv[2]
code = """import bpy,json
print(json.dumps({'filepath':bpy.data.filepath,'is_dirty':bpy.data.is_dirty,
'version':bpy.app.version_string,'build_hash':bpy.app.build_hash.decode(),
'dependencies':bpy.utils.blend_paths(absolute=True,packed=False,local=False),
'objects':[{'name':o.name,'type':o.type,'location':list(o.location),
'dimensions':list(o.dimensions),'object_id':o.get('proscenium_object_id'),
'instance_id':o.get('proscenium_instance_id')} for o in bpy.context.scene.objects]}))
"""
with socket.create_connection(("127.0.0.1", 9877), timeout=10) as stream:
    stream.sendall(json.dumps({"type": "execute_code", "params": {"code": code}}).encode())
    data = b""
    while len(data) < 1024 * 1024:
        chunk = stream.recv(65536)
        if not chunk:
            raise RuntimeError("Incomplete bridge reply")
        data += chunk
        try:
            reply = json.loads(data)
        except ValueError:
            continue
        break
if reply["status"] != "success":
    raise RuntimeError("Native readback failed")
result = json.loads(reply["result"]["result"])
source = root / "project/scenes/loop/scene.blend"
assert Path(result["filepath"]) == source
result.update(
    captured_at=datetime.now(UTC).isoformat(),
    source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
)
(root / f"native-{label}.json").write_text(json.dumps(result, indent=2))
print(json.dumps(result, indent=2))
