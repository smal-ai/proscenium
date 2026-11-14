"""Official catalogue/control read, zero sessions and zero model sends."""

import asyncio
import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "tools/proofs/copilot"))
from probe import close_client, make_client  # noqa: E402


async def main(root):
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    cli = REPO / "node_modules/@github/copilot-linux-x64/copilot"
    assert (
        hashlib.sha256(cli.read_bytes()).hexdigest()
        == "463978cdea4ad6973b615f1ba07b7837634218632480ef06307ea844ee369a69"
    )
    client = make_client(cli, root)
    try:
        await asyncio.wait_for(client.start(), 30)
        auth = await client.get_auth_status()
        models = await client.list_models()
        result = {
            "authenticated": auth.isAuthenticated,
            "auth_type": auth.authType,
            "root_sends": 0,
            "sessions_created": 0,
            "models": [],
        }
        for model in models:
            result["models"].append(
                {
                    name: getattr(model, name, None)
                    for name in ("id", "name", "billing", "capabilities")
                }
            )

        def encode(value):
            if hasattr(value, "to_dict"):
                return value.to_dict()
            import dataclasses

            if dataclasses.is_dataclass(value):
                return dataclasses.asdict(value)
            if hasattr(value, "value"):
                return value.value
            raise TypeError(type(value).__name__)

        (root / "catalogue.json").write_text(json.dumps(result, default=encode, indent=2))
        print(json.dumps(result, default=encode))
    finally:
        await close_client(client)


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]).resolve()))
