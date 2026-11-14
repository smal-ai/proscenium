"""ONE pinned public official checkpoint, downloaded only to isolated development storage."""

import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path

import httpx

ROOT = Path.home() / ".cache/proscenium-development/image-generation-light"
MODEL_ROOT = ROOT / "models"
REPOSITORY = "stabilityai/sd-turbo"
REVISION = "b261bac6fd2cf515557d5d0707481eafa0485ec2"
FILENAME = "sd_turbo.safetensors"
SHA256 = "3f067a1b943cf162f2b8f8588f6cf5824bd5b4c7d1d88d87164b9ca123616549"
BYTES = 5_214_561_328
URL = f"https://huggingface.co/{REPOSITORY}/resolve/{REVISION}/{FILENAME}?download=true"
MODEL = MODEL_ROOT / "checkpoints" / FILENAME


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def inspect_weights() -> dict:
    if MODEL.is_symlink() or MODEL.stat().st_size != BYTES or digest(MODEL) != SHA256:
        raise ValueError("Pinned official SD-Turbo checkpoint mismatch")
    with MODEL.open("rb") as stream:
        length = int.from_bytes(stream.read(8), "little")
        if not 0 < length < 32 * 1024**2:
            raise ValueError("Invalid safetensors header")
        header = json.loads(stream.read(length))
    types: dict[str, int] = {}
    for key, tensor in header.items():
        if key != "__metadata__":
            types[tensor["dtype"]] = (
                types.get(tensor["dtype"], 0)
                + tensor["data_offsets"][1]
                - tensor["data_offsets"][0]
            )
    return dict(
        repository=REPOSITORY,
        revision=REVISION,
        filename=FILENAME,
        bytes=BYTES,
        sha256=SHA256,
        tensor_payload_bytes_by_dtype=types,
        private=False,
        gated=False,
        source_url=URL,
        licence="Stability AI Community License, 2024-07-05",
        fit_floor_bytes=max(12 * 1024**3, BYTES + 8 * 1024**3),
    )


def download() -> None:
    MODEL.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if MODEL.exists():
        print(json.dumps(inspect_weights(), indent=2))
        return
    partial = MODEL.with_suffix(MODEL.suffix + ".part")
    start = partial.stat().st_size if partial.exists() else 0
    if start > BYTES or shutil.disk_usage(MODEL.parent).free < BYTES - start + 2 * 1024**3:
        raise ValueError("Invalid partial download or insufficient isolated disk space")
    # No account/token/header discovery. Signed CDN redirect URLs are never logged or saved.
    with httpx.Client(timeout=120, follow_redirects=True, trust_env=False) as client:
        for name in ("README.md", "LICENSE.md"):
            path = ROOT / name
            data = (
                client.get(f"https://huggingface.co/{REPOSITORY}/raw/{REVISION}/{name}")
                .raise_for_status()
                .content
            )
            if path.exists():
                if path.read_bytes() != data:
                    raise ValueError("Pinned documentation mismatch")
            else:
                path.write_bytes(data)
        with client.stream(
            "GET", URL, headers={"Range": f"bytes={start}-"} if start else {}
        ) as response:
            response.raise_for_status()
            if start and (
                response.status_code != 206
                or not response.headers.get("content-range", "").startswith(f"bytes {start}-")
            ):
                raise ValueError("Server did not honour exact partial resume; no blind re-download")
            with partial.open("ab" if start else "xb") as stream:
                received = start
                for chunk in response.iter_bytes(1024**2):
                    received += len(chunk)
                    if received > BYTES:
                        raise ValueError("Response exceeds the pinned <=6-GiB candidate")
                    stream.write(chunk)
                stream.flush()
                os.fsync(stream.fileno())
    if partial.stat().st_size != BYTES or digest(partial) != SHA256:
        raise ValueError("Incomplete/corrupt download retained for inspection; no other candidate")
    partial.chmod(0o400)
    os.link(partial, MODEL)
    partial.unlink()
    metadata = inspect_weights()
    (ROOT / "weights.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    try:
        if args.download:
            download()
        else:
            print(json.dumps(inspect_weights(), indent=2))
    except httpx.HTTPError as error:
        status = error.response.status_code if isinstance(error, httpx.HTTPStatusError) else None
        raise SystemExit(
            f"Public candidate HTTP failure: {type(error).__name__}, status={status}. Partial retained; no alternate candidate."
        ) from None
