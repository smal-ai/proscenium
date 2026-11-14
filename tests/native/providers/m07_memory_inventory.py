"""Read safetensors headers and host memory only. Never imports torch or calls a provider."""

import hashlib
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[3]
COMFY = Path("/home/quintin/.local/opt/ComfyUI")
MODELS = (
    "diffusion_models/flux-2-klein-4b.safetensors",
    "diffusion_models/flux-2-klein-9b-kv-fp8.safetensors",
    "text_encoders/qwen_3_4b.safetensors",
    "text_encoders/qwen_3_8b_fp8mixed.safetensors",
    "vae/flux2-vae.safetensors",
)


def header(name: str) -> dict:
    path = COMFY / "models" / name
    with path.open("rb") as stream:
        length = int.from_bytes(stream.read(8), "little")
        if not 0 < length < 32 * 1024**2:
            raise ValueError("Unexpected safetensors header size")
        raw = stream.read(length)
    value = json.loads(raw)
    by_dtype: dict[str, int] = {}
    for key, tensor in value.items():
        if key == "__metadata__":
            continue
        size = tensor["data_offsets"][1] - tensor["data_offsets"][0]
        by_dtype[tensor["dtype"]] = by_dtype.get(tensor["dtype"], 0) + size
    return dict(
        model=name,
        file_bytes=path.stat().st_size,
        header_bytes=length,
        header_sha256=hashlib.sha256(raw).hexdigest(),
        tensor_payload_bytes_by_dtype=by_dtype,
        tensor_count=len(value) - int("__metadata__" in value),
    )


def main() -> None:
    inventory = {name: header(name) for name in MODELS}
    mem = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
    available = int(mem["MemAvailable"].split()[0]) * 1024
    weights4 = inventory[MODELS[0]]["tensor_payload_bytes_by_dtype"]
    assert set(weights4) == {"BF16"}
    unet_fp8_estimate = weights4["BF16"] // 2
    clip4 = sum(inventory[MODELS[2]]["tensor_payload_bytes_by_dtype"].values())
    vae = sum(inventory[MODELS[4]]["tensor_payload_bytes_by_dtype"].values())
    floor = unet_fp8_estimate + clip4 + vae + 4 * 1024**3
    root = Path("/home/quintin/.cache/proscenium-development/providers-w07-20261114")
    original_hashes = {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in ("w07-live-initial-report.json", "w07-live-initial-timing.json")
    }
    result = dict(
        observed_at=datetime.now(ZoneInfo("Africa/Johannesburg")).isoformat(),
        read_scope="Only safetensors headers/stat, installed nodes.py/sd.py, /proc/meminfo and original receipt hashes. No tensor loading, inference or service effect.",
        host_mem_available_bytes=available,
        protected_headroom_bytes=4 * 1024**3,
        original_cold_fit_floor_bytes=20_427_268_612,
        original_floor_changed=False,
        original_files_sha256=original_hashes,
        inventory=inventory,
        proposed_new_graph_delta={"UNETLoader.weight_dtype": "fp8_e4m3fn"},
        proposal_scope="ONLY a separately authorised new explicit native generation task/graph; never mutate the original frozen job.",
        estimates={
            "ideal_packed_unet_fp8_payload_bytes": unet_fp8_estimate,
            "unchanged_clip4_payload_bytes": clip4,
            "unchanged_vae_payload_bytes": vae,
            "nominal_all_resident_payload_plus_headroom_bytes": floor,
            "nominal_shortfall_against_observation_bytes": max(0, floor - available),
            "caveat": "Arithmetic estimate, NOT measured runtime peak or a rigorous lower bound. Excludes casting overlap, scales, activations and allocator/cache effects. Normal offload/mmap may change residency; not tested.",
        },
        existing_encoder8_is_larger_than_encoder4_bytes=inventory[MODELS[3]]["file_bytes"]
        - inventory[MODELS[2]]["file_bytes"],
        native_9b_bundle_file_bytes_plus_headroom=inventory[MODELS[1]]["file_bytes"]
        + inventory[MODELS[3]]["file_bytes"]
        + inventory[MODELS[4]]["file_bytes"]
        + 4 * 1024**3,
        safe_fit_proven=False,
        additional_model_loads=0,
        additional_provider_posts=0,
        original_window_reopened=False,
        independent_processes_touched=False,
        official_sources=[
            "https://docs.comfy.org/tutorials/flux/flux-2-klein",
            "https://raw.githubusercontent.com/Comfy-Org/ComfyUI/master/nodes.py",
        ],
        installed_source_sha256={
            str(path.relative_to(COMFY)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (COMFY / "nodes.py", COMFY / "comfy/sd.py")
        },
    )
    output = REPO / "docs/evidence/m07/lower-memory-analysis.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(
            "Preserve this analysis snapshot; use a distinct explicitly scoped observation for any future comparison"
        )
    output.write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                "host_mem_available_bytes": available,
                "candidate_nominal_floor_bytes": floor,
                "safe_fit_proven": False,
                "additional_inference": 0,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
