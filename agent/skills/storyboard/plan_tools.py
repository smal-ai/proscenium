"""Read-only planning checks over the installed normative contract. No execution tools."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from proscenium.contracts import validate
from proscenium.shot_plan_impact import compare
from proscenium.shot_plan_impact import impact as plan_impact


def load(path: str) -> dict[str, Any]:
    plan = json.loads(Path(path).read_text(encoding="utf-8"))
    validate("AuthoredShotPlan", plan)
    shot_ids: set[str] = set()
    frame_ids: set[str] = set()
    for shot in plan["shots"]:
        if shot["shot_id"] in shot_ids:
            raise ValueError(f"Duplicate shot_id: {shot['shot_id']}")
        shot_ids.add(shot["shot_id"])
        roles = []
        for frame in shot["frames"]:
            if frame["frame_id"] in frame_ids:
                raise ValueError(f"Duplicate frame_id: {frame['frame_id']}")
            frame_ids.add(frame["frame_id"])
            roles.append({"start": 0, "intermediate": 1, "end": 2}[frame["role"]])
            if frame["state"] in {"available", "selected"} and frame["artifact_id"] is None:
                raise ValueError(f"Available frame has no artifact_id: {frame['frame_id']}")
        if roles != sorted(roles):
            raise ValueError(f"Frame roles out of order: {shot['shot_id']}")
        source_range = shot["source_range"]
        if source_range and source_range["start"] > source_range["end"]:
            raise ValueError(f"Reversed source_range: {shot['shot_id']}")
    return plan


def impact(plan: dict[str, Any], changed: set[str]) -> dict[str, Any]:
    return {**plan_impact(plan, changed), "conservative_plan_dependencies": True}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("validate", "compare", "impact"):
        child = commands.add_parser(command)
        child.add_argument("plan")
        if command == "compare":
            child.add_argument("after")
        if command == "impact":
            child.add_argument("--changed-id", action="append", required=True)
    args = parser.parse_args()
    try:
        plan = load(args.plan)
        if args.command == "compare":
            result = compare(plan, load(args.after))
        elif args.command == "impact":
            result = impact(plan, set(args.changed_id))
        else:
            result = {"valid": True, "plan_id": plan["plan_id"], "shot_count": len(plan["shots"])}
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except Exception as error:
        print(json.dumps({"valid": False, "error": str(error)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
