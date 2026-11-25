"""Resolve published assembly inputs. Creative sequence decisions remain authored sources."""

from typing import Any

from proscenium.api.errors import DomainError
from proscenium.auth import Caller
from proscenium.contracts import validate
from proscenium.previews.assembly import expand, input_ids, technical, with_audio
from proscenium.services import Services
from proscenium.sources.common import fingerprint


def prepared(services: Services, caller: Caller, body: dict[str, Any]) -> tuple[dict, dict, dict]:
    project_id = str(caller.project_id)
    catalogue = services.extension("catalogue")
    version = catalogue.version(project_id, body["version"]["version_id"])
    validate("VersionRecord", version)
    if version["project_id"] != project_id:
        raise DomainError("scope_denied", 403)
    if version["item_id"] != body["version"]["item_id"] or version["kind"] != "sequence":
        raise DomainError("source_conflict", 409)
    sequence = catalogue.sequence_map(project_id, version["version_id"])
    validate("SequenceMap", sequence)
    if (
        sequence["project_id"] != project_id
        or sequence["sequence_version_id"] != version["version_id"]
    ):
        raise DomainError("scope_denied", 403)
    maps = catalogue.frame_maps(project_id, sequence)
    full = expand(sequence, next(iter(maps.values())) if maps else None)
    validate("FrameMap", full)
    if any(value != full for value in maps.values()):
        raise DomainError("invalid_frame_mapping", 422)
    previews = services.extension("previews")
    inputs: dict[str, dict] = {}
    # Read each source job's receipts once, not once for every sequence frame.
    with services.registry.project_store(project_id).transaction(write=False) as tx:
        by_job = {}
        for job_id in {c["job_id"] for row in full["frames"] for c in row["contributors"]}:
            job = tx.require("jobs", job_id)
            validate("JobRecord", job)
            if job["project_id"] != project_id:
                raise DomainError("scope_denied", 403)
            index = {}
            for receipt in previews.records(tx, job, "frames"):
                validate("FrameReceipt", receipt)
                if (
                    receipt["project_id"] != project_id
                    or receipt["input_digest"] != job["input_digest"]
                    or receipt["job_id"] != job_id
                ):
                    raise DomainError("source_conflict", 409)
                key = (receipt["version_id"], receipt["shot_id"], receipt["source_frame"])
                if key in index:
                    raise DomainError("frame_conflict", 409)
                index[key] = receipt
            by_job[job_id] = index
        for row in full["frames"]:
            for contributor in row["contributors"]:
                key = (
                    contributor["version_id"],
                    contributor["shot_id"],
                    contributor["source_frame"],
                )
                receipt = by_job[contributor["job_id"]].get(key)
                if receipt is None:
                    raise DomainError("media_unavailable", 422)
                old = inputs.get(receipt["artifact_id"])
                if old is not None and old != receipt:
                    raise DomainError("frame_conflict", 409)
                inputs[receipt["artifact_id"]] = receipt
    snapshot = dict(
        sequence=technical(sequence),
        frame_map=full,
        input_frames=[inputs[key] for key in sorted(inputs)],
    )
    with_audio(snapshot, previews.artifacts, project_id)
    admission = dict(
        schema_version="1.0",
        operation_id=body["operation_id"],
        project_id=project_id,
        job_id=body["job_id"],
        kind="encode",
        target=dict(target_id=version["item_id"], version=body["version"], shot_id=None),
        input_digest=fingerprint(snapshot),
        input_artifact_ids=input_ids(snapshot),
        execution_profile_id=body["execution_profile_id"],
    )
    validate("JobAdmission", admission)
    return admission, sequence, full
