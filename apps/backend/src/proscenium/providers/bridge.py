"""Exact-attempt provider output registration. This is not a worker-to-tool token exchange."""

import hashlib
import os
import stat
from pathlib import Path, PurePosixPath
from typing import Any, cast

from proscenium.api.errors import DomainError
from proscenium.auth import Caller
from proscenium.catalogue.artifacts import Artifacts
from proscenium.contracts import validate
from proscenium.db import Transaction
from proscenium.services import Services
from proscenium.sources.common import managed, replay

JSON = dict[str, Any]


def _worker(tx: Transaction, caller: Caller) -> JSON:
    job = tx.require("jobs", str(caller.job_id))
    attempt = tx.require("attempts", str(caller.attempt_id))
    if (
        job["project_id"] != caller.project_id
        or job["attempt_id"] != caller.attempt_id
        or job["input_digest"] != caller.input_digest
        or attempt["job_id"] != caller.job_id
        or attempt["input_digest"] != caller.input_digest
        or attempt["project_id"] != caller.project_id
        or job["kind"] not in {"image_generation", "mesh_generation", "vlm_review"}
    ):
        raise DomainError("scope_denied", 403)
    return job


def _read(root: Path, relative: str) -> bytes:
    """Validate containment/secrets, then open every component without following symlinks."""
    managed(root, relative, exists=True)
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parts = PurePosixPath(relative).parts
        for component in parts[:-1]:
            child = os.open(
                component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
            )
            os.close(descriptor)
            descriptor = child
        file = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        with os.fdopen(file, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise DomainError("scope_denied", 403)
            return stream.read()
    except OSError as exc:
        raise DomainError("scope_denied", 403) from exc
    finally:
        os.close(descriptor)


def register_provider_artifact(services: Services, caller: Caller, request: JSON) -> JSON:
    """Promote one bound job's bytes through W02, returning the normative Artifact only.

    Parent binding: lambda caller, body: register_provider_artifact(services, caller, body).
    Replay checks precede file/status checks. No generic W02.register call or capability minting.
    """
    validate("ArtifactRegister", request)
    project_id = request["project_id"]
    caller.permits("registerToolArtifact", project_id)
    if caller.channel != "worker" or project_id != services.registry.active_id():
        raise DomainError("scope_denied", 403)
    immutable = {
        "request": request,
        "project_id": caller.project_id,
        "job_id": caller.job_id,
        "attempt_id": caller.attempt_id,
        "input_digest": caller.input_digest,
    }
    store = services.registry.project_store(project_id)
    with store.transaction(write=False) as tx:
        previous = replay(tx, "providers.artifact", request["operation_id"], immutable)
        if previous is not None:
            return previous
        job = _worker(tx, caller)
        derived = {}
        for transform in request.get("derived_from", []):
            for identity in transform["input_artifact_ids"]:
                artifact = tx.require("artifacts", identity)
                if artifact["project_id"] != project_id or artifact["state"] != "ready":
                    raise DomainError("media_unavailable", 422)
                derived[identity] = artifact
        version = job["target"]["version"]
        if version:
            record = tx.require("versions", version["version_id"])
            if record["project_id"] != project_id or record["item_id"] != version["item_id"]:
                raise DomainError("scope_denied", 403)
    prefix = f".proscenium/jobs/{job['job_id']}/"
    if not request["staging_path"].startswith(prefix):
        raise DomainError("scope_denied", 403)
    root = Path(services.registry.get(project_id)["path"])
    data = _read(root, request["staging_path"])
    if hashlib.sha256(data).hexdigest() != request["sha256"]:
        raise DomainError("hash_mismatch", 422)
    artifacts = cast(Artifacts, services.extension("artifacts"))
    for identity in derived:
        artifacts.ready_path(project_id, identity)
    artifact = artifacts.promote(
        project_id,
        request["artifact_id"],
        data,
        kind=request["kind"],
        purpose=request["purpose"],
        media_type=request["media_type"],
        derived_from=request.get("derived_from"),
        metadata={"source_version_id": version["version_id"]} if version else None,
    )
    with store.transaction() as tx:

        def effect() -> JSON:
            _worker(tx, caller)
            for identity, original in derived.items():
                if tx.require("artifacts", identity) != original:
                    raise DomainError("source_conflict", 409)
            artifacts.put(tx, artifact)
            return artifact

        return tx.operation("providers.artifact", request["operation_id"], immutable, effect)[0]
