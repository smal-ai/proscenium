"""Print an API graph for registration as a project artefact. Does not submit inference."""

import argparse
import json

from proscenium.providers.klein import graph


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for model in ("unet", "clip", "vae"):
        parser.add_argument("--" + model, required=True)
    parser.add_argument("--width", required=True, type=int)
    parser.add_argument("--height", required=True, type=int)
    parser.add_argument("--steps", default=4, type=int)
    parser.add_argument("--reference-artifact-id")
    print(json.dumps(graph(**vars(parser.parse_args())), indent=2))


if __name__ == "__main__":
    main()
