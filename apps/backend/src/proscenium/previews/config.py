"""Private host settings, never request-supplied executable commands."""

import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

from proscenium.api.errors import DomainError
from proscenium.catalogue.validation import interval, rational
from proscenium.contracts import validate

REVISION = "proscenium-blender-1"


@dataclass(frozen=True)
class Rendering:
    blender: str = "blender"
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    build_hash: str = "3f6797307da5"
    profile_id: str = "blender-cpu"
    threads: int = 4
    timeout: int = 300
    encode_timeout: int = 60
    max_frames: int = 100000
    max_pixels: int = 4096 * 4096
    max_samples: int = 4096
    claim_path: str = "/tmp/opencode/proscenium-native-claims/blender-render.lock"

    def __post_init__(self) -> None:
        if not (
            1 <= self.threads <= 32
            and 1 <= self.timeout <= 3600
            and 1 <= self.encode_timeout <= 600
            and self.build_hash
        ):
            raise ValueError("Rendering requires a bounded, pinned host allocation")

    def snapshot(self) -> dict:
        result = asdict(self)
        for key in ("blender", "ffmpeg", "ffprobe"):
            executable = shutil.which(result[key])
            if not executable:
                raise DomainError("capability_unavailable", 501, f"Missing installed {key}")
            result[key] = str(Path(executable).resolve(strict=True))
        return result

    def validate(self, spec: dict) -> None:
        validate("RenderSpec", spec)
        count = interval(spec["frames"])
        rational(spec["fps"])
        dimensions = spec["dimensions"]
        if (
            spec["renderer_revision"] != REVISION
            or spec["engine"] != "CYCLES"
            or spec["device"] != "CPU"
        ):
            raise DomainError(
                "capability_unavailable",
                501,
                "This installed profile is Cycles CPU. No GPU fallback is permitted",
            )
        if (
            count > self.max_frames
            or dimensions["width"] * dimensions["height"] > self.max_pixels
            or spec["samples"] is None
            or spec["samples"] > self.max_samples
        ):
            raise DomainError("invalid_request", message="Render exceeds configured profile bounds")
        if spec["purpose"] in {"draft_motion", "final"} and (
            dimensions["width"] % 2 or dimensions["height"] % 2
        ):
            raise DomainError("invalid_request", message="H.264 motion requires even dimensions")
        if spec["purpose"] == "proxy" and count != 1:
            raise DomainError("invalid_request", message="A proxy is a single static sample")
        if spec.get("extra_outputs"):
            raise DomainError("capability_unavailable", 501, "Extra outputs are not configured")
