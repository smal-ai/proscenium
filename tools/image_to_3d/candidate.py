"""One pinned, official native ComfyUI Hunyuan3D 2.0 candidate."""

from pathlib import Path

REPO = "tencent/Hunyuan3D-2"
REVISION = "9cd649ba6913f7a852e3286bad86bfa9a2d83dcf"
REMOTE_FILE = "hunyuan3d-dit-v2-0/model.fp16.safetensors"
FILENAME = "hunyuan3d-dit-v2_fp16.safetensors"
SIZE = 4_928_151_562
SHA256 = "360bc281fc956d4acac0c3d36d5ec0ebf8cdddbf4b8892e894d12419388d479b"
URL = f"https://huggingface.co/{REPO}/resolve/{REVISION}/{REMOTE_FILE}"
MODEL_ROOT = Path("/home/quintin/.cache/proscenium-development/image-to-3d/models")
WEIGHT_PATH = MODEL_ROOT / "checkpoints" / FILENAME
DOWNLOAD_CAP = 6 * 1024**3
DISK_RESERVE = 10 * 1024**3
COMFY_ROOT = Path("/home/quintin/.local/opt/ComfyUI")
COMFY_REVISION = "8ff6dc384ba5c410266b40e137799e049459d4f2"


def graph(reference_artifact_id: str) -> dict:
    """Trusted API graph, flattened from the official single-view template.

    The artefact placeholder must be replaced only by the budgeted provider's
    verified conditioning upload. This is not a directly runnable submit tool.
    """
    if not reference_artifact_id or any(c in reference_artifact_id for c in "/\\\x00"):
        raise ValueError("a project conditioning artefact ID is required")

    def node(kind: str, **inputs: object) -> dict:
        return {"class_type": kind, "inputs": inputs}

    return {
        "54": node("ImageOnlyCheckpointLoader", ckpt_name=FILENAME),
        "56": node("LoadImage", image=f"artifact:{reference_artifact_id}"),
        "51": node("CLIPVisionEncode", clip_vision=["54", 1], image=["56", 0], crop="center"),
        "80": node("Hunyuan3Dv2Conditioning", clip_vision_output=["51", 0]),
        "70": node("ModelSamplingAuraFlow", model=["54", 0], shift=1.0, sampling="flow"),
        "66": node("EmptyLatentHunyuan3Dv2", resolution=3072, batch_size=1),
        "3": node(
            "KSampler",
            model=["70", 0],
            seed="$seed",
            steps=20,
            cfg=8.0,
            sampler_name="euler",
            scheduler="normal",
            positive=["80", 0],
            negative=["80", 1],
            latent_image=["66", 0],
            denoise=1.0,
        ),
        "61": node(
            "VAEDecodeHunyuan3D",
            samples=["3", 0],
            vae=["54", 2],
            num_chunks=8000,
            octree_resolution=256,
        ),
        "81": node("VoxelToMesh", voxel=["61", 0], algorithm="surface net", threshold=0.6),
        "82": node("SaveGLB", mesh=["81", 0], filename_prefix="mesh/proscenium-e04"),
    }
