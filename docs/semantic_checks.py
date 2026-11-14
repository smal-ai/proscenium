"""Deterministic documentation oracles, not application/native validators."""

from fractions import Fraction
from math import gcd


def semantic_errors(definition, value, related=None):
    """Check named fixture invariants after successful JSON Schema validation."""
    related = related or {}
    errors = []

    def check(condition, code):
        if not condition and code not in errors:
            errors.append(code)

    if definition == "RequestRecord":
        submission, receipt = value["submission"], value["receipt"]
        for key in ("project_id", "request_id"):
            check(value[key] == submission[key] == receipt[key], "request_identity_mismatch")
        check(submission["type"] == receipt["type"], "request_identity_mismatch")
        if "submission" in related:
            check(submission == related["submission"], "request_submission_changed")
        if "context" in related and submission["type"] != "stop":
            check(submission["context_id"] == related["context"]["context_id"], "request_context_mismatch")

    if definition == "VersionRecord" and "publication" in related:
        publication = related["publication"]
        item = next((row for row in publication["items"] if row["version_id"] == value["version_id"]), None)
        check(item is not None, "version_not_published")
        if item:
            check(publication["project_id"] == value["project_id"], "version_identity_mismatch")
            check(publication["publication_id"] == value["publication_id"], "version_identity_mismatch")
            for key in ("item_id", "kind", "name", "source_commit", "source", "description", "tags",
                        "provenance", "unity", "derived_from", "preview_requests"):
                check((key in item) == (key in value) and item.get(key) == value.get(key),
                      "publication_metadata_lost")
            check(set(item["artifact_ids"]).issubset(value["artifact_ids"]), "publication_metadata_lost")

    if definition == "ContextSnapshot":
        selections = value["selected_objects"]
        pairs = [(row["object_id"], row["instance_id"]) for row in selections]
        check(len(pairs) == len(set(pairs)), "duplicate_instance_selection")
        if selections:
            check(value["item_id"] is not None and value["version_id"] is not None,
                  "selection_version_missing")
        if "source_fixture" in related:
            known = {(row["object_id"], row["instance_id"]) for row in related["source_fixture"]["scene_objects"]}
            check(set(pairs).issubset(known), "unknown_instance_selection")

    if definition == "FrameTarget" and value["origin"] == "sequence_render":
        check(value["version_id"] == value["sequence_version_id"], "sequence_target_mismatch")
        if "frame_map" in related:
            mapping = related["frame_map"]
            check(value["sequence_version_id"] == mapping["sequence_version_id"], "sequence_target_mismatch")
            check(1 <= value["sequence_frame"] <= mapping["frame_count"], "frame_out_of_range")

    if definition == "SequenceMap":
        cursor, entries = 1, value["entries"]
        check(len({entry["entry_id"] for entry in entries}) == len(entries), "duplicate_sequence_entry")
        for entry in entries:
            source, destination = entry["source_frames"], entry["sequence_frames"]
            check(source["start"] <= source["end"], "invalid_source_range")
            check(destination["start"] == cursor and destination["end"] >= destination["start"],
                  "sequence_partition_invalid")
            cursor = destination["end"] + 1
            if entry["transform"] in ("identity", "trim"):
                check(source["end"] - source["start"] == destination["end"] - destination["start"],
                      "implicit_retime")
            if entry["transform"] == "transition":
                check(len({row["shot_id"] for row in entry["contributors"]}) >= 2,
                      "transition_contributors_missing")
        check(cursor == value["frame_count"] + 1, "sequence_partition_invalid")
        if "frame_map" in related:
            errors.extend(sequence_pair_errors(value, related["frame_map"]))

    if definition == "FrameMap":
        check([row["sequence_frame"] for row in value["frames"]] == list(range(1, value["frame_count"] + 1)),
              "frame_map_coverage_invalid")
        for row in value["frames"]:
            weights = [Fraction(c["weight"]["numerator"], c["weight"]["denominator"]) for c in row["contributors"]]
            check(sum(weights) == 1, "frame_weights_invalid")
            check(all(gcd(c["weight"]["numerator"], c["weight"]["denominator"]) == 1 for c in row["contributors"]),
                  "frame_weights_not_reduced")
            keys = [(c["shot_id"], c["version_id"], c["job_id"], c["source_frame"]) for c in row["contributors"]]
            check(len(keys) == len(set(keys)), "duplicate_frame_contributor")

    if definition == "JobRecord":
        check((value["work_id"] is None) != (value["owner_operation_id"] is None), "job_authority_ambiguous")
        if value["work_id"] is None:
            check(value["kind"] == "vlm_review", "owner_job_kind_invalid")
        if value["kind"] == "render":
            check(value["target"]["version"] is not None and value["render_spec"] is not None,
                  "render_target_missing")

    if definition in ("SelectionSave", "SelectionRecord"):
        slot, choice = value["slot"], value["choice"]
        active = value.get("action") == "select" if definition == "SelectionSave" else value["state"] == "selected"
        check(active == (choice is not None), "selection_state_mismatch")
        if active and slot["kind"] == "item":
            check(choice["kind"] == "version" and choice["version"]["item_id"] == slot["item_id"],
                  "selection_item_mismatch")
        if active and slot["kind"] == "assembly_shot":
            check(choice["kind"] == "version", "selection_choice_invalid")
        if slot["kind"] == "storyboard_frame" and "shot_plan" in related:
            pairs = {(shot["shot_id"], frame["frame_id"]) for shot in related["shot_plan"]["shots"] for frame in shot["frames"]}
            check((slot["shot_id"], slot["frame_id"]) in pairs, "selection_slot_missing")

    if definition == "RenderSpec":
        check(value["frames"]["start"] <= value["frames"]["end"], "invalid_source_range")
        if value["purpose"] == "proxy":
            check(value["frames"]["start"] == value["frames"]["end"], "proxy_sample_ambiguous")

    return list(dict.fromkeys(errors))


def sequence_pair_errors(sequence, mapping):
    """Validate the declared sequence against its explicit per-frame source map."""
    errors = semantic_errors("FrameMap", mapping)
    if (sequence["project_id"] != mapping["project_id"]
            or sequence["sequence_version_id"] != mapping["sequence_version_id"]
            or sequence["frame_count"] != mapping["frame_count"]
            or Fraction(**_fraction_args(sequence["fps"])) != Fraction(**_fraction_args(mapping["fps"]))):
        errors.append("sequence_map_identity_mismatch")
    rows = {row["sequence_frame"]: row for row in mapping["frames"]}
    for entry in sequence["entries"]:
        destination = entry["sequence_frames"]
        expected = entry.get("contributors", []) if entry["transform"] == "transition" else [entry]
        allowed = {(c["shot_id"], c["version_id"], c.get("job_id")): c["source_frames"] for c in expected}
        for frame in range(destination["start"], destination["end"] + 1):
            row = rows.get(frame)
            if row is None:
                errors.append("frame_map_coverage_invalid")
                continue
            actual = {(c["shot_id"], c["version_id"], c["job_id"]) for c in row["contributors"]}
            if actual != set(allowed) or len(row["contributors"]) != len(allowed):
                errors.append("frame_contributor_mismatch")
            for contributor in row["contributors"]:
                bounds = allowed.get((contributor["shot_id"], contributor["version_id"], contributor["job_id"]))
                if not bounds or not bounds["start"] <= contributor["source_frame"] <= bounds["end"]:
                    errors.append("contributor_out_of_range")
                if entry["transform"] in ("identity", "trim"):
                    expected_frame = entry["source_frames"]["start"] + frame - destination["start"]
                    if contributor["source_frame"] != expected_frame:
                        errors.append("identity_frame_mismatch")
    return list(dict.fromkeys(errors))


def _fraction_args(rate):
    return {"numerator": rate["numerator"], "denominator": rate["denominator"]}
