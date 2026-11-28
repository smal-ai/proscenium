"""Pure potential-impact analysis for validated storyboard documents."""

from collections.abc import Callable
from typing import Any

from rfc8785 import dumps


def _report(
    plan: dict[str, Any], matches: Callable[[dict[str, Any]], bool]
) -> dict[str, list[str]]:
    affected = [shot for shot in plan["shots"] if matches(shot)]
    frames = [frame for shot in affected for frame in shot["frames"]]
    return dict(
        affected_shot_ids=[shot["shot_id"] for shot in affected],
        affected_frame_ids=[frame["frame_id"] for frame in frames],
        downstream_artifact_ids=sorted({f["artifact_id"] for f in frames if f["artifact_id"]}),
        downstream_scene_version_ids=sorted(
            {shot["scene_version"]["version_id"] for shot in affected if shot["scene_version"]}
        ),
    )


def impact(plan: dict[str, Any], changed: set[str]) -> dict[str, list[str]]:
    """Match untyped IDs conservatively, without interpreting creative prose."""
    global_ids = {plan["plan_id"], *plan["brief_artifact_ids"]}
    for dependency in plan["asset_dependencies"]:
        global_ids.update(dependency.values())
    global_change = bool(changed & global_ids)

    def matches(shot: dict[str, Any]) -> bool:
        local_ids = {shot["shot_id"]}
        for ref in shot.get("planned_assets", []):
            local_ids.update(ref.values())
        if shot["scene_version"]:
            local_ids.update(shot["scene_version"].values())
        for frame in shot["frames"]:
            local_ids.add(frame["frame_id"])
            if frame["artifact_id"]:
                local_ids.add(frame["artifact_id"])
        return global_change or bool(changed & local_ids)

    return _report(plan, matches)


def _normalised_shot(shot: dict[str, Any]) -> dict[str, Any]:
    # Only planned pins are unordered. Do not change the authored document.
    return {
        **shot,
        "planned_assets": sorted(
            {(ref["item_id"], ref["version_id"]) for ref in shot.get("planned_assets", [])}
        ),
    }


def compare(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    old = {s["shot_id"]: s for s in before["shots"]}
    new = {s["shot_id"]: s for s in after["shots"]}
    all_ids = old.keys() | new.keys()
    # Order changes affect assembly, even if individual shots are unchanged.
    reordered = list(old) != list(new)
    global_change = any(
        dumps(before.get(key)) != dumps(after.get(key))
        for key in (
            "plan_id",
            "project_id",
            "treatment",
            "brief_artifact_ids",
            "asset_dependencies",
        )
    )
    changed = (
        all_ids
        if reordered or global_change
        else {
            key
            for key in all_ids
            if dumps(_normalised_shot(old[key])) != dumps(_normalised_shot(new[key]))
        }
    )
    # Compare knows the identity type. A changed shot ID is not a changed
    # asset/frame ID merely because the same string was used in both places.
    reports = [_report(plan, lambda shot: shot["shot_id"] in changed) for plan in (before, after)]
    return dict(
        changed_shot_ids=sorted(changed),
        removed_shot_ids=sorted(old.keys() - new.keys()),
        removed_frame_ids=sorted(
            {f["frame_id"] for s in old.values() for f in s["frames"]}
            - {f["frame_id"] for s in new.values() for f in s["frames"]}
        ),
        shot_order_changed=reordered,
        stable_plan_id=before["plan_id"] == after["plan_id"],
        **{key: sorted(set(reports[0][key]) | set(reports[1][key])) for key in reports[0]},
    )
