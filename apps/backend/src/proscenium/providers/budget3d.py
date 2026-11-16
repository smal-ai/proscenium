"""Pinned native Budget3D (Idol / Idol-Turbo) ComfyUI graph and bindings. No IO or lifecycle."""

import copy
import math
from pathlib import PurePosixPath
from typing import Any

TURBO_REVISION = "budget3d-idol-turbo-1"
IDOL_REVISION = "budget3d-idol-1"

TURBO_UNET = "budget3d_idol_turbo.safetensors"
IDOL_UNET = "budget3d_idol.safetensors"
CLIP_VISION = "dino_v3_vit_h.safetensors"
FLUX_VAE = "flux2-vae.safetensors"
SPLAT_VAE = "idol_vae_decoder_fp32.safetensors"
BG_REMOVAL = "birefnet.safetensors"

SHARED_HASHES: dict[str, str] = {
    CLIP_VISION: "a29ef35101a16966972a0d50732a6f3a608ff7cfffb2afa9bbe9007cb842cc53",
    FLUX_VAE: "d64f3a68e1cc4f9f4e29b6e0da38a0204fe9a49f2d4053f0ec1fa1ca02f9c4b5",
    SPLAT_VAE: "1f9d633f6555ec8dafaa495d768e9c2eaf697e2847ae5e6b88dce4c6801b4f9e",
    BG_REMOVAL: "9ab37426bf4de0567af6b5d21b16151357149139362e6e8992021b8ce356a154",
}

TURBO_HASHES: dict[str, str] = {
    TURBO_UNET: "4f0c31ed6979f80478ce75e5002c81353604bf8560d392fc4ea06687be193c05",
    **SHARED_HASHES,
}

IDOL_HASHES: dict[str, str] = {
    IDOL_UNET: "8d87669ab02823d6fde862b4f1a0be6628f862ec16bbdb263a0838b5bbc4c1fe",
    **SHARED_HASHES,
}


def models(*, turbo: bool = True) -> list[str]:
    return [
        TURBO_UNET if turbo else IDOL_UNET,
        CLIP_VISION,
        FLUX_VAE,
        SPLAT_VAE,
        BG_REMOVAL,
    ]


def graph(
    reference_artifact_id: str,
    *,
    turbo: bool = True,
    budget: float = 1.0,
    mode: str = "synthetic",
    steps: int | None = None,
    num_gaussians: int = 262_144,
    mesh_resolution: int = 384,
) -> dict[str, Any]:
    if not reference_artifact_id or any(c in reference_artifact_id for c in "/\\\x00"):
        raise ValueError("a project conditioning artefact ID is required")
    if (
        type(budget) not in {int, float}
        or isinstance(budget, bool)
        or not math.isfinite(budget)
        or not 0.0625 <= float(budget) <= 1.0
    ):
        raise ValueError("budget must be a finite number between 0.0625 and 1.0")
    if mode not in {"synthetic", "real"}:
        raise ValueError("mode must be 'synthetic' or 'real'")
    resolved_steps = (5 if turbo else 50) if steps is None else steps
    if type(resolved_steps) is not int or not 1 <= resolved_steps <= 50:
        raise ValueError("steps must be an integer between 1 and 50")
    if (
        type(num_gaussians) is not int
        or not 32_768 <= num_gaussians <= 1_048_576
        or num_gaussians % 32 != 0
    ):
        raise ValueError("num_gaussians must be a multiple of 32 between 32768 and 1048576")
    if (
        type(mesh_resolution) is not int
        or not 64 <= mesh_resolution <= 768
        or mesh_resolution % 16 != 0
    ):
        raise ValueError("mesh_resolution must be a multiple of 16 between 64 and 768")

    def node(kind: str, **inputs: object) -> dict[str, Any]:
        return {"class_type": kind, "inputs": inputs}

    return {
        "1": node("LoadImage", image=f"artifact:{reference_artifact_id}"),
        "2": node("LoadBackgroundRemovalModel", bg_removal_name=BG_REMOVAL),
        "3": node("RemoveBackground", bg_removal_model=["2", 0], image=["1", 0]),
        "4": node("Budget3DPreprocessImage", image=["1", 0], mask=["3", 0], mode=mode),
        "5": node("CLIPVisionLoader", clip_name=CLIP_VISION),
        "6": node("VAELoader", vae_name=FLUX_VAE),
        "7": node("VAEComputeDtype", vae=["6", 0], dtype="bf16"),
        "8": node(
            "TripoSplatConditioning",
            clip_vision=["5", 0],
            vae=["7", 0],
            image=["4", 0],
        ),
        "9": node("Budget3DLoader", unet_name=TURBO_UNET if turbo else IDOL_UNET),
        "10": node(
            "Budget3DSampler",
            budget3d_model=["9", 0],
            positive=["8", 0],
            negative=["8", 1],
            latent_image=["8", 2],
            seed="$seed",
            steps=resolved_steps,
            cfg=0.0 if turbo else 3.0,
            budget=round(float(budget), 4),
            rescale_t=3.0,
            warmup_steps=0,
        ),
        "11": node("VAELoader", vae_name=SPLAT_VAE),
        "12": node("VAEComputeDtype", vae=["11", 0], dtype="fp32"),
        "13": node(
            "VAEDecodeTripoSplat",
            samples=["10", 0],
            vae=["12", 0],
            num_gaussians=num_gaussians,
            seed="$seed",
        ),
        "14": node(
            "SplatToMesh",
            splat=["13", 0],
            resolution=mesh_resolution,
            kernel=5,
            smooth=3,
            level=0.4,
            min_component=500,
            min_opacity=0.02,
            color_sharpen=2.0,
            solid=True,
            fill=20.0,
        ),
        "15": node("SaveGLB", mesh=["14", 0], filename_prefix="mesh/proscenium-budget3d"),
    }


def recipe_graph(
    reference_artifact_id: str,
    revision: str = TURBO_REVISION,
    recipe: dict[str, Any] | None = None,
) -> dict[str, Any]:
    turbo = revision == TURBO_REVISION
    cfg = recipe or {}
    return graph(
        reference_artifact_id,
        turbo=turbo,
        budget=cfg.get("budget", 1.0),
        mode=cfg.get("mode", "synthetic"),
        steps=cfg.get("steps", 5 if turbo else 50),
        num_gaussians=cfg.get("num_gaussians", 262_144),
        mesh_resolution=cfg.get("mesh_resolution", 384),
    )


def bind(
    template: dict[str, Any],
    reference_artifact_id: str,
    uploaded_filename: str,
    seed: str,
    *,
    turbo: bool = True,
) -> dict[str, Any]:
    if not isinstance(template, dict):
        raise ValueError("only the pinned trusted Budget3D image-to-mesh graph is supported")
    try:
        mode = template["4"]["inputs"]["mode"]
        sample_inputs = template["10"]["inputs"]
        steps = sample_inputs["steps"]
        budget = sample_inputs["budget"]
        num_gaussians = template["13"]["inputs"]["num_gaussians"]
        mesh_resolution = template["14"]["inputs"]["resolution"]
    except (KeyError, TypeError) as exc:
        raise ValueError(
            "only the pinned trusted Budget3D image-to-mesh graph is supported"
        ) from exc

    expected = graph(
        reference_artifact_id,
        turbo=turbo,
        budget=budget,
        mode=mode,
        steps=steps,
        num_gaussians=num_gaussians,
        mesh_resolution=mesh_resolution,
    )
    if not seed.isascii() or not seed.isdecimal() or len(seed) > 16 or int(seed) > 2**53 - 1:
        raise ValueError("seed must be an exact JSON-safe decimal integer")
    seed_int = int(seed)
    with_seed = copy.deepcopy(expected)
    with_seed["10"]["inputs"]["seed"] = seed_int
    with_seed["13"]["inputs"]["seed"] = seed_int
    if template != expected and template != with_seed:
        raise ValueError("only the pinned trusted Budget3D image-to-mesh graph is supported")

    path = PurePosixPath(uploaded_filename)
    if (
        not uploaded_filename
        or path.is_absolute()
        or any(part in {"..", "."} for part in uploaded_filename.split("/"))
        or "\\" in uploaded_filename
        or "\x00" in uploaded_filename
    ):
        raise ValueError("unsafe uploaded image filename")
    with_seed["1"]["inputs"]["image"] = uploaded_filename
    return with_seed


def output_files(history_entry: dict[str, Any]) -> list[dict[str, Any]]:
    status = history_entry.get("status", {})
    if status.get("status_str") != "success" or status.get("completed") is not True:
        raise ValueError("provider has not reported successful terminal completion")
    entries = history_entry.get("outputs", {}).get("15", {}).get("3d", [])
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
