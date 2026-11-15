"""Read-only service capability and graph check. Never starts or stops ComfyUI."""

import argparse
import json
from pathlib import Path

import httpx
from proscenium.providers.comfy import ComfyClient, preflight


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--graph", required=True, type=Path)
    parser.add_argument("--model", action="append", required=True)
    args = parser.parse_args()
    with httpx.Client(timeout=15, follow_redirects=False, trust_env=False) as transport:
        client = ComfyClient(args.endpoint, transport)
        catalog = client.capabilities()
        preflight(json.loads(args.graph.read_text()), catalog, args.model)
        print(json.dumps({"endpoint": client.base, "preflight": "passed", "busy": client.busy()}))


if __name__ == "__main__":
    main()
