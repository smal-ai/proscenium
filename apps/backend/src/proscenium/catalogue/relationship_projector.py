"""Pure projection of exact publication evidence into disposable relationship rows."""

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from proscenium.db.store import Transaction, now

GROUPS = {
    relation: ordinal
    for ordinal, relation in enumerate(
        (
            "contains_shot",
            "contains_panel",
            "board_reference",
            "bound_scene",
            "sequence_input",
            "declared_dependency",
            "derived_from",
        )
    )
}
# Shot-local inputs share a group. Keep existing on-disk sort groups stable.
GROUPS["planned_asset"] = GROUPS["bound_scene"]
GROUPS["includes_source"] = GROUPS["declared_dependency"]
DOCUMENTS = {"storyboard": "AuthoredShotPlan", "sequence": "SequenceMap"}


def encoded(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def endpoint_key(endpoint: dict) -> tuple[str, str, str]:
    kind = endpoint["kind"]
    if kind == "version":
        return kind, endpoint["ref"]["version_id"], ""
    if kind == "artifact":
        return kind, "", endpoint["artifact_id"]
    local = (
        endpoint["shot_id"]
        if kind == "shot"
        else encoded([endpoint["shot_id"], endpoint["frame_id"]])
    )
    return kind, endpoint["board"]["version_id"], local


def version_ref(version: dict) -> dict:
    return {key: version[key] for key in ("item_id", "version_id")}


def version_endpoint(ref: dict) -> dict:
    return {"kind": "version", "ref": ref}


@dataclass(frozen=True)
class Projection:
    version_id: str
    item_id: str
    kind: str
    digest: str
    reasons: tuple[str, ...]
    edges: tuple[tuple, ...] = ()
    shots: tuple[tuple, ...] = ()
    panels: tuple[tuple, ...] = ()


def project_version_relationships(
    version: dict,
    document: dict | None = None,
    *,
    reason: str | None = None,
    inclusions_verified: bool = False,
) -> Projection:
    """Project a validated record and document. A reason permits pins-only projection."""
    board = version_ref(version)
    root = version_endpoint(board)
    version_id = board["version_id"]
    kind = version["kind"]
    pins = {ref["version_id"]: ref for ref in version["source"]["dependencies"]}
    specific: set[tuple[str, str]] = set()
    edges: dict[str, tuple] = {}
    shots = []
    panels = []

    def add(
        relation: str,
        source: dict,
        target: dict,
        details: dict | None = None,
        *,
        ordinal: int = 0,
    ) -> None:
        details = details if details is not None else {}
        key = encoded([relation, source, target, details])
        digest = sha256(key.encode("ascii")).hexdigest()
        # A sequence's version inputs group by pinned consumer on reverse reads,
        # while remaining in authored entry order on its outgoing page.
        sequence_pin = relation == "sequence_input" and target["kind"] == "version"
        tie = encoded([version_id, source.get("shot_id", ""), f"{ordinal:020d}", digest])
        edges.setdefault(
            key,
            (
                version_id,
                *endpoint_key(source),
                *endpoint_key(target),
                GROUPS[relation],
                0 if sequence_pin else ordinal,
                tie,
                relation,
                encoded(source),
                encoded(target),
                encoded(details),
            ),
        )
        if target["kind"] == "version" and relation in {
            "bound_scene",
            "board_reference",
            "planned_asset",
            "sequence_input",
        }:
            ref = target["ref"]
            specific.add((ref["item_id"], ref["version_id"]))

    if kind in DOCUMENTS and document is None and reason is None:
        raise ValueError("Structured publication requires a document or partial coverage")
    if kind == "storyboard" and document is not None:
        for ref in document["asset_dependencies"]:
            add("board_reference", root, version_endpoint(ref))
        for ordinal, shot in enumerate(document["shots"]):
            shot_id = shot["shot_id"]
            scene = shot["scene_version"]
            source = {"kind": "shot", "board": board, "shot_id": shot_id}
            shots.append(
                (
                    version_id,
                    shot_id,
                    board["item_id"],
                    ordinal,
                    shot["intent"],
                    "bound" if scene else "unbound",
                )
            )
            add("contains_shot", root, source, {"ordinal": ordinal}, ordinal=ordinal)
            for ref in shot.get("planned_assets", []):
                add("planned_asset", source, version_endpoint(ref), ordinal=1)
            if scene is not None:
                add(
                    "bound_scene",
                    source,
                    version_endpoint(scene),
                    {"source_range": shot["source_range"], "fps": shot["fps"]},
                )
            for frame_ordinal, frame in enumerate(shot["frames"]):
                frame_id = frame["frame_id"]
                panels.append(
                    (
                        version_id,
                        shot_id,
                        frame_id,
                        board["item_id"],
                        frame_ordinal,
                        frame["role"],
                        frame["composition"],
                        frame["state"],
                        frame["artifact_id"],
                    )
                )
                add(
                    "contains_panel",
                    source,
                    {**source, "kind": "panel", "frame_id": frame_id},
                    {
                        "ordinal": frame_ordinal,
                        **{k: frame[k] for k in ("role", "composition", "state", "artifact_id")},
                    },
                    ordinal=frame_ordinal,
                )
    elif kind == "sequence" and document is not None:
        for ordinal, entry in enumerate(document["entries"]):
            slot = {"entry_id": entry["entry_id"], "sequence_frames": entry["sequence_frames"]}
            ranges = {**slot, "source_frames": entry["source_frames"]}
            if "clip_artifact_id" in entry:
                add(
                    "sequence_input",
                    root,
                    {"kind": "artifact", "artifact_id": entry["clip_artifact_id"]},
                    {**ranges, "role": "clip"},
                    ordinal=ordinal,
                )
                continue
            add(
                "sequence_input",
                root,
                version_endpoint(pins[entry["version_id"]]),
                {**ranges, "role": "entry", "job_id": entry["job_id"], "shot_id": entry["shot_id"]},
                ordinal=ordinal,
            )
            for contributor in entry.get("contributors", []):
                add(
                    "sequence_input",
                    root,
                    version_endpoint(pins[contributor["version_id"]]),
                    {
                        **slot,
                        "role": "contributor",
                        "shot_id": contributor["shot_id"],
                        "job_id": contributor.get("job_id"),
                        "source_frames": contributor["source_frames"],
                    },
                    ordinal=ordinal,
                )
        for ordinal, track in enumerate(document.get("audio_tracks", []), len(document["entries"])):
            add(
                "sequence_input",
                root,
                {"kind": "artifact", "artifact_id": track["artifact_id"]},
                {
                    "role": "audio",
                    "track_id": track["track_id"],
                    "start_seconds": track["start_seconds"],
                },
                ordinal=ordinal,
            )
    if inclusions_verified:
        for ref in version["source"].get("included_sources", []):
            specific.add((ref["item_id"], ref["version_id"]))
            add("includes_source", root, version_endpoint(ref))
    for ref in version["source"]["dependencies"]:
        if (ref["item_id"], ref["version_id"]) not in specific:
            add("declared_dependency", root, version_endpoint(ref))
    for ref in version.get("derived_from", []):
        add("derived_from", root, version_endpoint(ref))

    return Projection(
        version_id,
        board["item_id"],
        kind,
        version["source"]["digest"],
        (reason,) if reason else (),
        tuple(edges.values()),
        tuple(shots),
        tuple(panels),
    )


def apply_version_projection(tx: Transaction, projection: Projection) -> None:
    """Replace one producer's projection in the caller's transaction; caller fences retries."""
    db = tx.connection
    version_id = projection.version_id
    revision = db.execute(
        "UPDATE relationship_meta SET relationship_revision=relationship_revision+1 WHERE id=1 "
        "RETURNING relationship_revision"
    ).fetchone()[0]
    db.execute("DELETE FROM relationship_edges WHERE producer_version_id=?", (version_id,))
    db.execute("DELETE FROM relationship_panels WHERE board_version_id=?", (version_id,))
    db.execute("DELETE FROM relationship_shots WHERE board_version_id=?", (version_id,))
    db.execute("DELETE FROM relationship_versions WHERE version_id=?", (version_id,))
    db.execute(
        "INSERT INTO relationship_versions VALUES (?,?,?,?,?,?,?,?)",
        (
            version_id,
            projection.item_id,
            projection.kind,
            "partial" if projection.reasons else "complete",
            encoded(projection.reasons),
            now(),
            projection.digest,
            revision,
        ),
    )
    db.executemany(
        "INSERT INTO relationship_edges VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", projection.edges
    )
    db.executemany("INSERT INTO relationship_shots VALUES (?,?,?,?,?,?)", projection.shots)
    db.executemany("INSERT INTO relationship_panels VALUES (?,?,?,?,?,?,?,?,?)", projection.panels)
    if projection.reasons == ("indexing",):
        db.execute(
            "INSERT INTO relationship_pending (version_id,stage) VALUES (?,2)", (version_id,)
        )
