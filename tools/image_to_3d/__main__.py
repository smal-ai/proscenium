"""Prepare/check image-to-mesh inputs. No command submits inference or starts ComfyUI."""

import argparse
import json
from pathlib import Path

from .candidate import graph
from .glb import inspect_glb
from .instance import plan
from .weights import download, inspect_weights
from .workflow import bind, output_files, preflight


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    graph_parser = commands.add_parser("graph")
    graph_parser.add_argument("--reference-artifact-id", required=True)
    commands.add_parser("inspect-weights")
    commands.add_parser("download-weights", help="explicit six-GiB isolated preparation allocation")
    glb_parser = commands.add_parser("inspect-glb")
    glb_parser.add_argument("path", type=Path)
    history_parser = commands.add_parser("inspect-history")
    history_parser.add_argument(
        "path", type=Path, help="one exact-prompt history entry, not all history"
    )
    check_parser = commands.add_parser("preflight")
    check_parser.add_argument(
        "--catalog", type=Path, required=True, help="saved actual object_info JSON"
    )
    check_parser.add_argument("--reference-artifact-id", required=True)
    plan_parser = commands.add_parser("instance-plan")
    plan_parser.add_argument("--run-root", type=Path, required=True)
    plan_parser.add_argument("--port", type=int, default=18588)
    args = parser.parse_args()
    result: object
    if args.command == "graph":
        result = graph(args.reference_artifact_id)
    elif args.command == "inspect-weights":
        result = inspect_weights()
    elif args.command == "download-weights":
        result = download()
    elif args.command == "inspect-glb":
        result = inspect_glb(args.path)
    elif args.command == "inspect-history":
        result = output_files(json.loads(args.path.read_text()))
    elif args.command == "preflight":
        template = graph(args.reference_artifact_id)
        bound = bind(template, args.reference_artifact_id, "conditioning-00.png", "1")
        preflight(bound, json.loads(args.catalog.read_text()))
        result = {"schemas_and_model_choices_valid": True, "inference_run": False}
    else:
        result = plan(args.run_root, args.port)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
