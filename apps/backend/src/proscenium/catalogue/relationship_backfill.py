"""Bounded background/recovery reads over the sealed publication, never the worktree."""

import json
import sqlite3
from subprocess import TimeoutExpired
from typing import Any

from proscenium.api.errors import DomainError
from proscenium.catalogue import validation
from proscenium.catalogue.relationship_projector import (
    DOCUMENTS,
    Projection,
    apply_version_projection,
    encoded,
    project_version_relationships,
)
from proscenium.contracts import validate
from proscenium.db.store import Transaction
from proscenium.sources.read_only import read_authored_json
from proscenium.sources.resolver import SourceReaderServices, closure_digest

MAX_RETRIES = 3


def has_pending_relationships(tx: Transaction, *, include_deferred: bool = False) -> bool:
    """Deferred work still affects coverage, but does not wake the lifecycle."""
    if include_deferred:
        return bool(
            tx.connection.execute("SELECT EXISTS(SELECT 1 FROM relationship_pending)").fetchone()[0]
        )
    return bool(
        tx.connection.execute(
            "SELECT EXISTS(SELECT 1 FROM relationship_pending WHERE retry_count<?)", (MAX_RETRIES,)
        ).fetchone()[0]
    )


def _retryable(exc: Exception) -> bool:
    return isinstance(exc, (TimeoutExpired, sqlite3.OperationalError)) or (
        isinstance(exc, DomainError)
        and (exc.status >= 500 or exc.code in {"storage_unavailable", "project_busy"})
    )


def _evidence_reason(exc: DomainError) -> str:
    if _retryable(exc):
        raise exc
    if (
        exc.code
        in {
            "invalid_request",
            "source_conflict",
            "scope_denied",
            "dependency_collision",
        }
        or exc.message == validation.PLANNED_ASSET_MISSING
        or exc.message == validation.AUTHORED_PIN_MISSING
        or isinstance(exc.__cause__, ValueError)
    ):
        return "invalid_historical_record"
    if exc.code in {
        "source_unavailable",
        "retained_source_missing",
        "hash_mismatch",
        "dependency_missing",
    }:
        return "source_unavailable"
    return "invalid_historical_record"


def _validate_document(version: dict, document: dict, tx: Transaction) -> None:
    if version["kind"] == "storyboard":
        validation.shot_plan(tx, document, {}, historical=True, version=version)
        return
    validation.sequence_map(tx, document, version["version_id"], None)
    validation.sequence_dependencies(tx, document, version)


def _historical_projection(
    services: SourceReaderServices, project_id: str, version_id: str, raw: str, *, stage: int
) -> Projection:
    try:
        version = validate("VersionRecord", json.loads(raw))
        if version["project_id"] != project_id or version["version_id"] != version_id:
            raise DomainError("invalid_request")
    except DomainError as exc:
        return Projection(version_id, "", "", "", (_evidence_reason(exc),))
    except (KeyError, TypeError, ValueError):
        return Projection(version_id, "", "", "", ("invalid_historical_record",))

    # Don't invent a label/kind for an invalid historical pin. This is separate
    # from missing *source bytes* on an otherwise valid target publication.
    reason = (
        None
        if closure_digest(version["source"]) == version["source"]["digest"]
        else "invalid_historical_record"
    )
    refs = version["source"]["dependencies"] + version.get("derived_from", [])
    if refs:
        with services.registry.project_store(project_id).transaction(write=False) as tx:
            known = {
                identity: json.loads(body)
                for identity, body in tx.connection.execute(
                    "SELECT id,body FROM records WHERE namespace='versions' AND "
                    "id IN (SELECT value FROM json_each(?))",
                    (encoded(sorted({ref["version_id"] for ref in refs})),),
                )
            }

        def exact(ref: dict) -> bool:
            row = known.get(ref["version_id"])
            return (
                isinstance(row, dict)
                and row.get("project_id") == project_id
                and row.get("item_id") == ref["item_id"]
                and row.get("version_id") == ref["version_id"]
                and row.get("kind") in ("asset", "scene", "storyboard", "sequence", "unity_result")
            )

        if not all(exact(ref) for ref in refs):
            version = {
                **version,
                "source": {
                    **version["source"],
                    "dependencies": [
                        ref for ref in version["source"]["dependencies"] if exact(ref)
                    ],
                },
                "derived_from": [ref for ref in version.get("derived_from", []) if exact(ref)],
            }
            reason = "invalid_historical_record"
    try:
        validation.publication_entry(version)
    except DomainError as exc:
        reason = _evidence_reason(exc)
    if reason or version["kind"] not in DOCUMENTS:
        return project_version_relationships(version, reason=reason)
    if stage == 1:
        return project_version_relationships(version, reason="indexing")

    try:
        document = read_authored_json(services, project_id, version)
    except OSError:
        reason = "source_unavailable"
    except (KeyError, TypeError, ValueError):
        reason = "invalid_historical_record"
    except DomainError as exc:
        reason = _evidence_reason(exc)
    else:
        try:
            with services.registry.project_store(project_id).transaction(write=False) as tx:
                _validate_document(version, document, tx)
            return project_version_relationships(version, document)
        except DomainError as exc:
            reason = _evidence_reason(exc)
        except (KeyError, TypeError, ValueError):
            reason = "invalid_historical_record"
        except OSError:
            reason = "source_unavailable"
    return project_version_relationships(version, reason=reason)


def _unchanged(tx: Transaction, projection: Projection, current: Any) -> bool:
    if current is None or tuple(current) != (
        projection.item_id,
        projection.kind,
        encoded(projection.reasons),
        projection.digest,
    ):
        return False
    for table, field, rows in (
        ("relationship_edges", "producer_version_id", projection.edges),
        ("relationship_shots", "board_version_id", projection.shots),
        ("relationship_panels", "board_version_id", projection.panels),
    ):
        stored = {
            tuple(row)
            for row in tx.connection.execute(
                f"SELECT * FROM {table} WHERE {field}=?",
                (projection.version_id,),
            )
        }
        if stored != set(rows):
            return False
    return True


def _record_retry(tx: Transaction, identity: str, stage: int) -> None:
    db = tx.connection
    db.execute(
        "INSERT INTO relationship_pending (version_id,stage,retry_count) VALUES (?,?,1) "
        "ON CONFLICT(version_id) DO UPDATE SET retry_count=retry_count+1",
        (identity, stage),
    )
    # An explicit rebuild may be retrying a previously indexed producer. Keep
    # all its evidence but mark the new check incomplete. Routine stage 2 rows
    # are already indexing and do not move the public revision on retries.
    if db.execute(
        "UPDATE relationship_versions SET coverage_state='partial',reasons_json='[\"indexing\"]' "
        "WHERE version_id=? AND reasons_json<>'[\"indexing\"]'",
        (identity,),
    ).rowcount:
        revision = db.execute(
            "UPDATE relationship_meta SET relationship_revision=relationship_revision+1 "
            "WHERE id=1 RETURNING relationship_revision"
        ).fetchone()[0]
        db.execute(
            "UPDATE relationship_versions SET indexed_revision=? WHERE version_id=?",
            (revision, identity),
        )


def _batch(
    services: SourceReaderServices,
    project_id: str,
    *,
    batch_size: int,
    after: str = "",
    rebuild: bool = False,
) -> tuple[int, str]:
    if not 1 <= batch_size <= 100:
        raise ValueError("Relationship batch size must be 1–100")
    store = services.registry.project_store(project_id)
    with store.transaction(write=False) as tx:
        if rebuild:
            candidates = tx.connection.execute(
                "SELECT r.id,r.body,r.revision,i.indexed_revision,2,NULL FROM relationship_versions i "
                "CROSS JOIN records r ON r.namespace='versions' AND r.id=i.version_id "
                "WHERE i.version_id>? AND i.reasons_json<>'[\"indexing\"]' ORDER BY i.version_id LIMIT ?",
                (after, batch_size),
            ).fetchall()
        else:
            # Keep each batch in one pass. Prefer untried work across both
            # passes so a failing Pass 1 producer cannot hold up Pass 2.
            candidates = tx.connection.execute(
                "SELECT p.version_id,r.body,r.revision,i.indexed_revision,p.stage,p.retry_count "
                "FROM relationship_pending p "
                "CROSS JOIN records r ON r.namespace='versions' AND r.id=p.version_id "
                "LEFT JOIN relationship_versions i ON i.version_id=p.version_id "
                "WHERE (p.retry_count,p.stage)=(SELECT retry_count,stage FROM relationship_pending "
                "WHERE retry_count<? ORDER BY retry_count,stage,version_id LIMIT 1) "
                "ORDER BY p.version_id LIMIT ?",
                (MAX_RETRIES, batch_size),
            ).fetchall()
    if not candidates:
        return 0, after
    prepared = []
    for identity, body, revision, indexed_revision, stage, attempts in candidates:
        try:
            projection = _historical_projection(services, project_id, identity, body, stage=stage)
        except (DomainError, TimeoutExpired, sqlite3.OperationalError) as exc:
            if not _retryable(exc):
                raise
            projection = None
        prepared.append((identity, body, revision, indexed_revision, stage, attempts, projection))
    with store.transaction() as tx:
        for identity, body, revision, indexed_revision, stage, attempts, projection in prepared:
            current = tx.connection.execute(
                "SELECT r.body,r.revision,i.indexed_revision,p.retry_count,i.item_id,i.kind,i.reasons_json,i.source_digest "
                "FROM records r LEFT JOIN relationship_versions i ON i.version_id=r.id "
                "LEFT JOIN relationship_pending p ON p.version_id=r.id "
                "WHERE r.namespace='versions' AND r.id=?",
                (identity,),
            ).fetchone()
            if current is None or tuple(current)[:4] != (
                body,
                revision,
                indexed_revision,
                attempts,
            ):
                continue  # Another writer (or a changed record) won after the read.
            if projection is None:
                _record_retry(tx, identity, stage)
                continue
            try:
                with tx.savepoint():
                    if indexed_revision is None or not _unchanged(tx, projection, current[4:]):
                        apply_version_projection(tx, projection)
            except (DomainError, TimeoutExpired, sqlite3.OperationalError) as exc:
                if not _retryable(exc):
                    raise
                _record_retry(tx, identity, stage)
    return len(candidates), candidates[-1][0]


def backfill_relationships(
    services: SourceReaderServices, project_id: str, *, batch_size: int = 25
) -> int:
    """Process at most one batch; committed queue removals are the restart position."""
    count, _ = _batch(services, project_id, batch_size=batch_size)
    return count


def rebuild_relationships(
    services: SourceReaderServices, project_id: str, *, batch_size: int = 25
) -> None:
    """Explicitly recheck all producers, including partial ones, in bounded batches."""
    with services.registry.project_store(project_id).transaction() as tx:
        tx.connection.execute("UPDATE relationship_pending SET retry_count=0 WHERE retry_count>0")
    after = ""
    while True:
        count, after = _batch(
            services, project_id, batch_size=batch_size, after=after, rebuild=True
        )
        if count < batch_size:
            break
    while backfill_relationships(services, project_id, batch_size=batch_size):
        pass
