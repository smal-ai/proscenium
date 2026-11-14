"""One pinned, official native ComfyUI Hunyuan3D 2.0 candidate."""

from pathlib import Path

from proscenium.providers.hunyuan import FILENAME as FILENAME
from proscenium.providers.hunyuan import graph as graph

REPO = "tencent/Hunyuan3D-2"
REVISION = "9cd649ba6913f7a852e3286bad86bfa9a2d83dcf"
REMOTE_FILE = "hunyuan3d-dit-v2-0/model.fp16.safetensors"
SIZE = 4_928_151_562
SHA256 = "360bc281fc956d4acac0c3d36d5ec0ebf8cdddbf4b8892e894d12419388d479b"
URL = f"https://huggingface.co/{REPO}/resolve/{REVISION}/{REMOTE_FILE}"
MODEL_ROOT = Path("/home/quintin/.cache/proscenium-development/image-to-3d/models")
WEIGHT_PATH = MODEL_ROOT / "checkpoints" / FILENAME
DOWNLOAD_CAP = 6 * 1024**3
DISK_RESERVE = 10 * 1024**3
COMFY_ROOT = Path("/home/quintin/.local/opt/ComfyUI")
COMFY_REVISION = "8ff6dc384ba5c410266b40e137799e049459d4f2"
