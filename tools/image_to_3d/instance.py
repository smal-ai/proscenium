"""Describe, never start, a future explicitly allocated isolated ComfyUI process."""

import socket
import subprocess
from pathlib import Path

from .candidate import COMFY_REVISION, COMFY_ROOT, MODEL_ROOT


def plan(run_root: Path, port: int = 18588) -> dict:
    root = run_root.resolve()
    allowed = MODEL_ROOT.parent / "runs"
    if not root.is_relative_to(allowed) or root == allowed or run_root != root:
        raise ValueError(f"run root must be a canonical new child of {allowed}")
    if root.exists():
        raise ValueError("fresh instance directory required, preserve previous evidence")
    if not 1024 <= port <= 65535 or port == 8188:
        raise ValueError("an unprivileged, independent port is required")
    if (COMFY_ROOT / "extra_model_paths.yaml").exists():
        raise ValueError("installed code auto-loads extra_model_paths.yaml, isolation needs review")
    revision = subprocess.check_output(
        ["git", "-C", str(COMFY_ROOT), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    if revision != COMFY_REVISION:
        raise ValueError("installed ComfyUI revision changed")
    dirty = subprocess.check_output(
        ["git", "-C", str(COMFY_ROOT), "status", "--porcelain", "--untracked-files=no"],
        text=True,
    )
    if dirty:
        raise ValueError("installed ComfyUI has tracked modifications")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", port))
    argv = [
        str(COMFY_ROOT / ".venv/bin/python"),
        "-B",
        str(COMFY_ROOT / "main.py"),
        "--listen",
        "127.0.0.1",
        "--port",
        str(port),
        "--base-directory",
        str(root),
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
    return {
        "argv": argv,
        "cwd": str(root),
        "create_before_authorised_start": [
            str(root / name)
            for name in (
                "user",
                "input",
                "output",
                "temp",
                "cache",
                "cache/torch",
                "cache/hf",
            )
        ],
        "environment_overrides": {
            "OMP_NUM_THREADS": "4",
            "MKL_NUM_THREADS": "4",
            "PYTHONDONTWRITEBYTECODE": "1",
            "XDG_CACHE_HOME": str(root / "cache"),
            "TORCH_HOME": str(root / "cache/torch"),
            "HF_HOME": str(root / "cache/hf"),
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "TMPDIR": str(root / "temp"),
        },
        "executed": False,
        "requires": "parent allocation, heavy-gpu claim, budgeted plugin and recheck of port/revision",
    }
