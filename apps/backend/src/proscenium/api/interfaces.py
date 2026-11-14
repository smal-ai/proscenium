"""Feature service seams, not fabricated successful endpoints or parallel DTOs."""

from pathlib import Path
from typing import Any, Protocol

from proscenium.auth import Caller
from proscenium.db import Transaction


class ArtifactService(Protocol):
    def get(self, project_id: str, artifact_id: str) -> dict[str, Any]: ...
    def ready_path(self, project_id: str, artifact_id: str) -> Path: ...


class SourceService(Protocol):
    def materialise(self, project_id: str, version_id: str) -> Path: ...
    def checkpoint(self, caller: Caller, request: dict[str, Any]) -> dict[str, Any]: ...


class ReviewStore(Protocol):
    def create(self, tx: Transaction, request: dict[str, Any], job_id: str) -> dict[str, Any]: ...
    def complete(self, caller: Caller, request: dict[str, Any]) -> dict[str, Any]: ...


class ContextService(Protocol):
    def get(self, project_id: str, context_id: str) -> dict[str, Any]: ...
    def validate_references(self, tx: Transaction, context: dict[str, Any]) -> None: ...
