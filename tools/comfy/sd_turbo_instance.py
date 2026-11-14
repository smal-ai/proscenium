"""Pure isolated-instance plan. Starting it needs the separate bounded native allocation."""

import socket
import subprocess
from pathlib import Path

from tools.comfy.sd_turbo_candidate import MODEL_ROOT, ROOT

COMFY_ROOT = Path("/home/quintin/.local/opt/ComfyUI")
COMFY_REVISION = "8ff6dc384ba5c410266b40e137799e049459d4f2"
ALLOCATION = "m07-lightweight-20261114"
RUN_ROOT = ROOT / "runs" / ALLOCATION


def plan() -> dict:
    root = RUN_ROOT.resolve()
    if root != RUN_ROOT or root.exists():
        raise ValueError("Preserve prior evidence; a canonical fresh allocation root is required")
    if MODEL_ROOT.resolve() != MODEL_ROOT:
        raise ValueError("The isolated model root must not redirect to another installation")
    extra = COMFY_ROOT / "extra_model_paths.yaml"
    if extra.exists() or extra.is_symlink():
        raise ValueError(
            "Installed code auto-loads extra_model_paths.yaml; isolation not established"
        )
    revision = subprocess.check_output(
        ["git", "-C", str(COMFY_ROOT), "rev-parse", "HEAD"], text=True
    ).strip()
    dirty = subprocess.check_output(
        ["git", "-C", str(COMFY_ROOT), "status", "--porcelain", "--untracked-files=no"], text=True
    )
    if revision != COMFY_REVISION or dirty:
        raise ValueError("Pinned installed ComfyUI source changed")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 18589))
    argv = [
        str(COMFY_ROOT / ".venv/bin/python"),
        "-B",
        str(COMFY_ROOT / "main.py"),
        "--listen",
        "127.0.0.1",
        "--port",
        "18589",
        "--base-directory",
        str(root / "comfy"),
        "--models-directory",
        str(MODEL_ROOT),
        "--user-directory",
        str(root / "user"),
        "--input-directory",
        str(root / "input"),
        "--output-directory",
        str(root / "output"),
        "--temp-directory",
        str(root / "temp"),
        "--database-url",
        f"sqlite:///{root / 'user/comfyui.db'}",
        "--disable-all-custom-nodes",
        "--disable-api-nodes",
        "--disable-auto-launch",
        "--cache-none",
    ]
    return dict(
        allocation=ALLOCATION,
        root=str(root),
        argv=argv,
        cwd=str(root),
        source_revision=revision,
        endpoint="http://127.0.0.1:18589",
        maximum_process_seconds=1800,
        create_before_start=[
            str(root / name)
            for name in [
                "comfy",
                "user",
                "input",
                "output",
                "temp",
                "cache",
                "cache/torch",
                "cache/hf",
            ]
        ],
        environment_overrides=dict(
            OMP_NUM_THREADS="4",
            MKL_NUM_THREADS="4",
            OPENBLAS_NUM_THREADS="4",
            PYTHONDONTWRITEBYTECODE="1",
            XDG_CACHE_HOME=str(root / "cache"),
            TORCH_HOME=str(root / "cache/torch"),
            HF_HOME=str(root / "cache/hf"),
            HF_HUB_OFFLINE="1",
            TRANSFORMERS_OFFLINE="1",
            TMPDIR=str(root / "temp"),
        ),
        independent_8188_mutation=False,
        extra_model_paths_present=False,
        executed=False,
    )
