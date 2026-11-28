"""Read one page of exact, indexed relationships without touching source or media."""

import json
import re
from hashlib import sha256
from typing import Any

from proscenium.api.errors import DomainError
from proscenium.catalogue.relationship_backfill import has_pending_relationships
from proscenium.catalogue.relationship_projector import (
    encoded,
    endpoint_key,
    version_endpoint,
    version_ref,
)
from proscenium.contracts import canonical, validate
from proscenium.db.store import Transaction
from proscenium.services import Services

SORT_VERSION = 1
KINDS = {"asset", "scene", "storyboard", "sequence", "unity_result"}
REASONS = ("invalid_historical_record", "source_unavailable")
Keyset = tuple[int, int, str]


def edge_query(direction: str) -> str:
    side = "from" if direction == "uses" else "to"
    return (
        "SELECT sort_group,sort_ordinal,sort_tie,relation,from_json,to_json,details_json "
        f"FROM relationship_edges WHERE {side}_kind=? AND {side}_version_id=? AND {side}_local_id=? "
        "AND (sort_group,sort_ordinal,sort_tie)>(?,?,?) "
        "ORDER BY sort_group,sort_ordinal,sort_tie LIMIT ?"
    )


def _after(services: Services, scope: str, cursor: str | None, revision: int) -> Keyset:
    if cursor is None:
        return -1, -1, ""
    try:
        if len(cursor) > 2048 or not re.fullmatch(r"[A-Za-z0-9_-]+", cursor):
            raise ValueError()
        raw = services.cursors.decode(scope, cursor)
        if services.cursors.encode(scope, raw) != cursor:
            raise ValueError()
        seen, group, ordinal, tie = json.loads(raw)
        if any(type(n) is not int or not 0 <= n < 2**53 for n in (seen, group, ordinal)):
            raise ValueError()
        if not isinstance(tie, str) or not tie:
            raise ValueError()
    except (ValueError, KeyError, TypeError) as exc:
        raise DomainError("invalid_cursor") from exc
    if seen != revision:
        raise DomainError(
            "relationship_revision_changed",
            409,
            "Relationships changed. Restart at page one.",
            safe_for_model=True,
        )
    return group, ordinal, tie


def _coverage(tx: Transaction, direction: str, indexed: Any) -> dict:
    if direction == "uses":
        reasons = json.loads(indexed[1]) if indexed else ["indexing"]
    else:
        reasons = ["indexing"] if has_pending_relationships(tx, include_deferred=True) else []
        # A producer currently records at most one failure reason. Probe exact
        # indexed values rather than walking every partial producer on each page.
        for reason in REASONS:
            if tx.connection.execute(
                "SELECT 1 FROM relationship_versions WHERE coverage_state='partial' AND reasons_json=? LIMIT 1",
                (encoded([reason]),),
            ).fetchone():
                reasons.append(reason)
    return {"state": "partial" if reasons else "complete", "reasons": sorted(reasons)}


def _records(tx: Transaction, namespace: str, ids: set[str]) -> dict[str, dict]:
    if not ids:
        return {}
    return {
        identity: record
        for identity, body in tx.connection.execute(
            "SELECT id,body FROM records WHERE namespace=? AND id IN (SELECT value FROM json_each(?))",
            (namespace, encoded(sorted(ids))),
        )
        if isinstance(record := json.loads(body), dict)
    }


def _slots(tx: Transaction, slots: dict[str, dict]) -> dict[str, dict]:
    index = _records(tx, "review.selection_slots", set(slots))
    records = _records(tx, "selections", {row["selection_id"] for row in index.values()})
    return {
        key: record["choice"]
        for key, row in index.items()
        if (record := records.get(row["selection_id"]))
        and record.get("project_id") == tx.project_id
        and record.get("slot") == slots[key]
        and record.get("state") == "selected"
        and isinstance(record.get("choice"), dict)
    }


def _hash_slot(slot: dict) -> str:
    return sha256(canonical(slot)).hexdigest()


def _structural(tx: Transaction, table: str, columns: tuple[str, ...], ids: set[tuple]) -> dict:
    if not ids:
        return {}
    joins = " AND ".join(
        f"p.{col}=json_extract(key.value,'$[{i}]')" for i, col in enumerate(columns)
    )
    return {
        tuple(row[col] for col in columns): row
        for row in tx.connection.execute(
            f"SELECT p.* FROM json_each(?) key CROSS JOIN {table} p ON {joins}",
            (encoded(sorted(ids)),),
        )
    }


def _exact(tx: Transaction, record: dict | None, ref: dict) -> bool:
    return bool(
        isinstance(record, dict)
        and record.get("project_id") == tx.project_id
        and all(record.get(key) == value for key, value in ref.items())
    )


def _nodes(tx: Transaction, root: dict, version: dict, items: list[dict]) -> list[dict]:
    endpoints = {encoded(root): root}
    for edge in items:
        for name in ("from", "to"):
            endpoint = edge[name]
            endpoints.setdefault(encoded(endpoint), endpoint)
    for endpoint in tuple(endpoints.values()):
        if endpoint["kind"] in {"shot", "panel"}:
            owner = version_endpoint(endpoint["board"])
            endpoints.setdefault(encoded(owner), owner)
    nodes = list(endpoints.values())
    refs = [node["ref"] for node in nodes if node["kind"] == "version"]
    shots = _structural(
        tx,
        "relationship_shots",
        ("board_version_id", "shot_id"),
        {
            (node["board"]["version_id"], node["shot_id"])
            for node in nodes
            if node["kind"] == "shot"
        },
    )
    panels = _structural(
        tx,
        "relationship_panels",
        ("board_version_id", "shot_id", "frame_id"),
        {
            (node["board"]["version_id"], node["shot_id"], node["frame_id"])
            for node in nodes
            if node["kind"] == "panel"
        },
    )

    def slot(node: dict) -> dict | None:
        if node["kind"] == "version":
            return {"kind": "item", "item_id": node["ref"]["item_id"]}
        if node["kind"] == "panel":
            return {
                "kind": "storyboard_frame",
                "storyboard_version": node["board"],
                "shot_id": node["shot_id"],
                "frame_id": node["frame_id"],
            }
        return None

    node_slots = {encoded(node): value for node in nodes if (value := slot(node)) is not None}
    keys = {key: _hash_slot(value) for key, value in node_slots.items()}
    choices = _slots(tx, {keys[key]: value for key, value in node_slots.items()})
    panel_keys = {keys[key] for key, node in endpoints.items() if node["kind"] == "panel"}
    panel_choices = [
        choice["version"]
        for key, choice in choices.items()
        if choice["kind"] == "version" and key in panel_keys
    ]
    versions = _records(
        tx,
        "versions",
        {
            ref["version_id"]
            for ref in [*refs, *panel_choices]
            if ref["version_id"] != version["version_id"]
        },
    )
    versions[version["version_id"]] = version
    catalogue = _records(tx, "items", {ref["item_id"] for ref in refs})
    artifact_ids = {node["artifact_id"] for node in nodes if node["kind"] == "artifact"}
    artifact_ids.update(
        row["artifact_id"] for row in panels.values() if row["artifact_id"] is not None
    )
    for ref in panel_choices:
        pinned = versions.get(ref["version_id"])
        if _exact(tx, pinned, ref) and pinned is not None:
            artifact_ids.update(pinned["artifact_ids"])
    artifacts = _records(tx, "artifacts", artifact_ids)

    def artefact(identity: str | None) -> dict:
        if identity is None:
            return {}
        row = artifacts.get(identity)
        return row if row and _exact(tx, row, {"artifact_id": identity}) else {}

    result = []
    for key, node in endpoints.items():
        choice = choices.get(keys.get(key, ""), {})
        kind = node["kind"]
        if kind == "version":
            ref = node["ref"]
            pinned = versions.get(ref["version_id"])
            if not _exact(tx, pinned, ref) or pinned is None or pinned.get("kind") not in KINDS:
                raise DomainError("source_conflict", 409)
            item = catalogue.get(ref["item_id"])
            result.append(
                {
                    **node,
                    "label": pinned.get("name") or ref["version_id"],
                    "item_kind": pinned["kind"],
                    "is_current": bool(
                        item
                        and _exact(tx, item, {"item_id": ref["item_id"]})
                        and item.get("current_version_id") == ref["version_id"]
                    ),
                    "is_selected": choice == {"kind": "version", "version": ref},
                }
            )
        elif kind == "shot":
            row = shots.get((node["board"]["version_id"], node["shot_id"]))
            if row is not None and row["board_item_id"] != node["board"]["item_id"]:
                raise DomainError("source_conflict", 409)
            intent = row["intent"] if row is not None else ""
            result.append(
                {
                    **node,
                    "label": intent or node["shot_id"],
                    "intent": intent,
                    "binding_status": row["binding_status"] if row is not None else "unknown",
                }
            )
        elif kind == "panel":
            row = panels.get((node["board"]["version_id"], node["shot_id"], node["frame_id"]))
            if row is None or row["board_item_id"] != node["board"]["item_id"]:
                raise DomainError("source_conflict", 409)
            artifact_id = row["artifact_id"]
            chosen = choice.get("artifact_id")
            if choice.get("kind") == "version":
                ref = choice["version"]
                pinned = versions.get(ref["version_id"])
                if _exact(tx, pinned, ref) and pinned is not None:
                    images = [
                        a for a in pinned["artifact_ids"] if artefact(a).get("kind") == "image"
                    ]
                    chosen = images[0] if len(images) == 1 else None
            result.append(
                {
                    **node,
                    "label": row["composition"] or node["frame_id"],
                    "role": row["role"],
                    "authored_state": row["authored_state"],
                    "artifact_id": artifact_id,
                    "artifact_state": artefact(artifact_id).get("state", "missing"),
                    "owner_selected": artifact_id is not None and artifact_id == chosen,
                }
            )
        else:
            artifact = artefact(node["artifact_id"])
            result.append(
                {
                    **node,
                    "label": node["artifact_id"],
                    "artifact_kind": artifact.get("kind"),
                    "purpose": artifact.get("purpose"),
                    "state": artifact.get("state", "missing"),
                }
            )
    return result


def read_relationships(
    services: Services,
    project_id: str,
    version_id: str,
    *,
    direction: str = "uses",
    shot_id: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> dict:
    store = services.registry.project_store(project_id)
    if direction not in {"uses", "used_by"}:
        raise DomainError(
            "invalid_request", message="Direction must be uses or used_by.", safe_for_model=True
        )
    if not 1 <= limit <= 100:
        raise DomainError(
            "invalid_request", message="Limit must be between 1 and 100.", safe_for_model=True
        )
    if shot_id is not None:
        validate("Id", shot_id)
    with store.transaction(write=False) as tx:
        version = tx.require("versions", version_id)
        if not _exact(tx, version, {"version_id": version_id}):
            raise DomainError("not_found", 404)
        revision = tx.connection.execute(
            "SELECT relationship_revision FROM relationship_meta WHERE id=1"
        ).fetchone()[0]
        indexed = tx.connection.execute(
            "SELECT coverage_state,reasons_json FROM relationship_versions WHERE version_id=?",
            (version_id,),
        ).fetchone()
        ref = version_ref(version)
        root = version_endpoint(ref)
        if shot_id is not None:
            if version["kind"] != "storyboard":
                raise DomainError(
                    "invalid_request",
                    message="Shot roots require a storyboard version.",
                    safe_for_model=True,
                )
            root = {"kind": "shot", "board": ref, "shot_id": shot_id}
        scope = encoded(["relationships", SORT_VERSION, project_id, direction, root])
        after = _after(services, scope, cursor, revision)
        if (
            shot_id is not None
            and indexed
            and indexed[0] == "complete"
            and not tx.connection.execute(
                "SELECT 1 FROM relationship_shots WHERE board_version_id=? AND shot_id=? AND board_item_id=?",
                (version_id, shot_id, ref["item_id"]),
            ).fetchone()
        ):
            raise DomainError("not_found", 404)
        coverage = _coverage(tx, direction, indexed)
        rows = tx.connection.execute(
            edge_query(direction), (*endpoint_key(root), *after, limit + 1)
        ).fetchall()
        edges = [
            {
                "relation": row[3],
                "from": json.loads(row[4]),
                "to": json.loads(row[5]),
                "details": json.loads(row[6]),
            }
            for row in rows[:limit]
        ]
        next_cursor = (
            services.cursors.encode(scope, encoded([revision, *rows[limit - 1][:3]]))
            if len(rows) > limit
            else None
        )
        return {
            "schema_version": "1.0",
            "project_id": project_id,
            "root": root,
            "direction": direction,
            "relationship_revision": revision,
            "coverage": coverage,
            "items": edges,
            "nodes": _nodes(tx, root, version, edges),
            "next_cursor": next_cursor,
        }
