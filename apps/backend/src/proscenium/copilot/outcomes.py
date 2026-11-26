"""Trusted host tool outcomes and deterministic retained-output provenance."""

from contextlib import nullcontext
from typing import Any

from proscenium.api.errors import DomainError
from proscenium.contracts import validate
from proscenium.db import Store, Transaction

NAMESPACE = "copilot.tool_outcomes"


def record_tool_outcome(
    tx_or_store: Transaction | Store,
    *,
    invocation_id: str,
    session_id: str | None,
    project_id: str,
    work_id: str | None,
    request_id: str | None,
    tool_name: str,
    status: str,
    artifact_ids: list[str] | tuple[str, ...] = (),
    publication_id: str | None = None,
    published_versions: list[dict[str, str]] | tuple[dict[str, str], ...] = (),
    job_ids: list[str] | tuple[str, ...] = (),
    error_code: str | None = None,
    phase: str | None = None,
    safe_summary: str = "",
    issues: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
) -> dict[str, Any]:
    """Create or CAS-update one invocation. Identical replays perform no write.

    Callers supply sanitised host diagnostics, never model claims or provider bodies.
    Attribution cannot be rebound when a later phase updates the same outcome.
    """
    for value in [invocation_id, project_id, *artifact_ids, *job_ids]:
        validate("Id", value)
    for value in [work_id, request_id, publication_id]:
        validate("NullableId", value)
    for ref in published_versions:
        validate("VersionRef", ref)
    body = dict(
        schema_version="1.0",
        invocation_id=invocation_id,
        session_id=session_id,
        project_id=project_id,
        work_id=work_id,
        request_id=request_id,
        tool_name=tool_name,
        status=status,
        artifact_ids=sorted(set(artifact_ids)),
        publication_id=publication_id,
        published_versions=[
            {"item_id": item, "version_id": version}
            for item, version in sorted(
                {(ref["item_id"], ref["version_id"]) for ref in published_versions}
            )
        ],
        job_ids=sorted(set(job_ids)),
        error_code=error_code,
        phase=phase,
        safe_summary=safe_summary,
        issues=list(issues),
    )
    manager = (
        tx_or_store.transaction() if isinstance(tx_or_store, Store) else nullcontext(tx_or_store)
    )
    with manager as tx:
        old = get_tool_outcome(tx, invocation_id)
        if old:
            if any(
                old[key] != body[key]
                for key in (
                    "invocation_id",
                    "session_id",
                    "project_id",
                    "work_id",
                    "request_id",
                    "tool_name",
                )
            ):
                raise DomainError("idempotency_conflict", 409)
            if old == body:
                return old
        tx.put(
            NAMESPACE,
            invocation_id,
            body,
            expected_revision=tx.revision(NAMESPACE, invocation_id) if old else None,
        )
        return body


def get_tool_outcome(tx: Transaction, invocation_id: str) -> dict[str, Any] | None:
    return tx.get(NAMESPACE, invocation_id, required=False)


def artifact_attributions(
    tx: Transaction, *, artifact_ids: set[str] | None = None
) -> dict[str, dict[str, Any]]:
    """Join recorded associations only. Never infer an owner request from recency.

    Singular attribution prefers a trusted tool outcome, then publication, then job,
    each in storage-ID order. Published versions are the sorted union of recorded
    associations. Unassociated legacy artefacts retain null identities. A page of
    already-loaded artefacts can supply its IDs to skip the artefact scan.
    """
    identities = (
        [artifact["artifact_id"] for artifact in tx.all("artifacts")]
        if artifact_ids is None
        else sorted(artifact_ids)
    )
    if not identities:
        return {}
    result: dict[str, dict[str, Any]] = {
        identity: dict(
            attribution_status="unrecorded",
            request_id=None,
            work_id=None,
            invocation_id=None,
            job_id=None,
            published_versions=[],
        )
        for identity in identities
    }
    versions = {version["version_id"]: version for version in tx.all("versions")}
    publications = tx.all("publications")
    jobs = {job["job_id"]: job for job in tx.all("jobs")}

    def associate(artifact_id: str, provenance: dict[str, Any], refs: list[dict[str, Any]]) -> None:
        if artifact_id not in result:
            return
        row = result[artifact_id]
        if row["attribution_status"] == "unrecorded" and any(
            provenance.get(key) for key in ("request_id", "work_id", "job_id", "publication_id")
        ):
            row.update(
                {
                    key: provenance.get(key)
                    for key in ("request_id", "work_id", "invocation_id", "job_id")
                }
            )
            row["attribution_status"] = "verified"
        elif row["attribution_status"] == "verified":
            keys = ("request_id", "work_id", "invocation_id", "job_id")
            # Enrich only a compatible recorded association, never combine identities
            # belonging to unrelated invocations that happen to reuse the same bytes.
            compatible = not any(
                row[key] and provenance.get(key) and row[key] != provenance[key] for key in keys
            )
            linked = any(row[key] and row[key] == provenance.get(key) for key in keys)
            if compatible and linked:
                for key in keys:
                    if row[key] is None:
                        row[key] = provenance.get(key)
        pins = {(ref["item_id"], ref["version_id"]) for ref in row["published_versions"] + refs}
        row["published_versions"] = [
            {"item_id": item, "version_id": version} for item, version in sorted(pins)
        ]

    for outcome in tx.all(NAMESPACE):
        if outcome.get("project_id") != tx.project_id:
            continue
        for artifact_id in outcome.get("artifact_ids", []):
            if artifact_id not in result:
                continue
            # A job ID is attributable only when that job records this output.
            job_id = next(
                (
                    identity
                    for identity in sorted(outcome.get("job_ids", []))
                    if artifact_id in jobs.get(identity, {}).get("artifact_ids", [])
                ),
                None,
            )
            associate(
                artifact_id, {**outcome, "job_id": job_id}, outcome.get("published_versions", [])
            )
    for publication in publications:
        submission = publication.get("submission", {})
        for item in submission.get("items", []):
            ref = {"item_id": item["item_id"], "version_id": item["version_id"]}
            for artifact_id in item.get("artifact_ids", []):
                associate(
                    artifact_id,
                    {**publication, "publication_id": submission.get("publication_id")},
                    [ref],
                )
    for job_id, job in sorted(jobs.items()):
        if job.get("project_id") != tx.project_id:
            continue
        ref = job.get("target", {}).get("version")
        refs = [ref] if ref and ref["version_id"] in versions else []
        for artifact_id in job.get("artifact_ids", []):
            associate(artifact_id, {**job, "job_id": job_id}, refs)
    for version in versions.values():
        if version.get("project_id") != tx.project_id:
            continue
        ref = {"item_id": version["item_id"], "version_id": version["version_id"]}
        for artifact_id in version.get("artifact_ids", []):
            associate(artifact_id, {"publication_id": version.get("publication_id")}, [ref])
    return result
