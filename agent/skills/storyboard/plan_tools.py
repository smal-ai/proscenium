"""Read-only planning checks over the installed normative contract. No execution tools."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from proscenium.contracts import canonical, validate


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
    global_ids = {plan["plan_id"], *plan["brief_artifact_ids"]}
    for dependency in plan["asset_dependencies"]:
        global_ids.update(dependency.values())
    affected = []
    frames = []
    artifacts = set()
    scenes = set()
    for shot in plan["shots"]:
        local_ids = {shot["shot_id"]}
        if shot["scene_version"]:
            local_ids.update(shot["scene_version"].values())
        for frame in shot["frames"]:
            local_ids.add(frame["frame_id"])
            if frame["artifact_id"]:
                local_ids.add(frame["artifact_id"])
        if changed & (global_ids | local_ids):
            affected.append(shot["shot_id"])
            frames.extend(frame["frame_id"] for frame in shot["frames"])
            artifacts.update(
                frame["artifact_id"] for frame in shot["frames"] if frame["artifact_id"]
            )
            if shot["scene_version"]:
                scenes.add(shot["scene_version"]["version_id"])
    return {
        "affected_shot_ids": affected,
        "affected_frame_ids": frames,
        "downstream_artifact_ids": sorted(artifacts),
        "downstream_scene_version_ids": sorted(scenes),
        "conservative_plan_dependencies": True,
    }


def compare(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    old = {shot["shot_id"]: shot for shot in before["shots"]}
    new = {shot["shot_id"]: shot for shot in after["shots"]}
    changed = {
        key for key in old.keys() | new.keys() if canonical(old.get(key)) != canonical(new.get(key))
    }
    # Array reorder affects sequence assembly, even when individual shot bytes match.
    reordered = list(old) != list(new)
    global_change = any(
        canonical(before.get(key)) != canonical(after.get(key))
        for key in (
            "plan_id",
            "project_id",
            "treatment",
            "brief_artifact_ids",
            "asset_dependencies",
        )
    )
    if global_change or reordered:
        changed.update(old.keys() | new.keys())
    reports = [impact(plan, changed) for plan in (before, after)]
    return {
        "changed_shot_ids": sorted(changed),
        "removed_shot_ids": sorted(old.keys() - new.keys()),
        "removed_frame_ids": sorted(
            {f["frame_id"] for s in old.values() for f in s["frames"]}
            - {f["frame_id"] for s in new.values() for f in s["frames"]}
        ),
        "shot_order_changed": reordered,
        "stable_plan_id": before["plan_id"] == after["plan_id"],
        **{
            key: sorted(set(reports[0][key]) | set(reports[1][key]))
            for key in (
                "affected_shot_ids",
                "affected_frame_ids",
                "downstream_artifact_ids",
                "downstream_scene_version_ids",
            )
        },
    }


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
