"""Pinned native Hunyuan3D graph and image-only bindings. No IO or lifecycle."""

import copy
from pathlib import PurePosixPath

FILENAME = "hunyuan3d-dit-v2_fp16.safetensors"


def graph(reference_artifact_id: str) -> dict:
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


def bind(template: dict, reference_artifact_id: str, uploaded_filename: str, seed: str) -> dict:
    expected = graph(reference_artifact_id)
    # Admission and post-upload binding accept the same already-bound exact seed.
    with_seed = copy.deepcopy(expected)
    if not seed.isascii() or not seed.isdecimal() or len(seed) > 16 or int(seed) > 2**53 - 1:
        raise ValueError("seed must be an exact JSON-safe decimal integer")
    with_seed["3"]["inputs"]["seed"] = int(seed)
    if template != expected and template != with_seed:
        raise ValueError("only the pinned trusted Hunyuan image-to-mesh graph is supported")
    path = PurePosixPath(uploaded_filename)
    if (
        not uploaded_filename
        or path.is_absolute()
        or any(part in {"..", "."} for part in uploaded_filename.split("/"))
        or "\\" in uploaded_filename
        or "\x00" in uploaded_filename
    ):
        raise ValueError("unsafe uploaded image filename")
    with_seed["56"]["inputs"]["image"] = uploaded_filename
    return with_seed


def output_files(history_entry: dict) -> list[dict]:
    status = history_entry.get("status", {})
    if status.get("status_str") != "success" or status.get("completed") is not True:
        raise ValueError("provider has not reported successful terminal completion")
    entries = history_entry.get("outputs", {}).get("82", {}).get("3d", [])
    if len(entries) != 1:
        raise ValueError("expected exactly one SaveGLB 3d output")
    for entry in entries:
        name, folder = entry.get("filename", ""), entry.get("subfolder", "")
        if (
            entry.get("type") != "output"
            or not name.endswith(".glb")
            or PurePosixPath(name).name != name
            or "\\" in name
            or "\x00" in name
            or PurePosixPath(folder).is_absolute()
            or "\\" in folder
            or "\x00" in folder
            or any(p in {"..", "."} for p in folder.split("/"))
        ):
            raise ValueError("unsafe or non-GLB provider output descriptor")
    return copy.deepcopy(entries)
