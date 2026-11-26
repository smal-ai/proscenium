"""Feature service seams, not fabricated successful endpoints or parallel DTOs."""

from pathlib import Path
from typing import Any, Protocol

from proscenium.auth import Caller
from proscenium.db import Transaction


class ArtifactService(Protocol):
    def promote(
        self,
        project_id: str,
        artifact_id: str,
        data: bytes,
        *,
        kind: str,
        purpose: str,
        media_type: str,
        derived_from: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...
    @staticmethod
    def put(tx: Transaction, artifact: dict[str, Any]) -> None: ...
    def get(self, project_id: str, artifact_id: str) -> dict[str, Any]: ...
    def ready_path(self, project_id: str, artifact_id: str) -> Path: ...
    def validate_mask(
        self, project_id: str, artifact_id: str, *, dimensions: dict[str, int], semantics: str
    ) -> dict[str, Any]: ...


class SourceService(Protocol):
    def materialise(self, project_id: str, version_id: str) -> Path: ...
    def checkpoint(self, caller: Caller, request: dict[str, Any]) -> dict[str, Any]: ...


class ReviewStore(Protocol):
    def create(
        self,
        tx: Transaction,
        request: dict[str, Any],
        job_id: str,
        *,
        caller: Caller | None = None,
    ) -> dict[str, Any]: ...
    def complete(
        self,
        caller: Caller,
        request: dict[str, Any],
        *,
        diagnostics: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...
    def complete_in_transaction(
        self,
        tx: Transaction,
        caller: Caller,
        request: dict[str, Any],
        *,
        diagnostics: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...
    def escalate(self, caller: Caller, body: dict[str, Any]) -> tuple[dict[str, Any], bool]: ...
    def ticket_view(self, tx: Transaction, ticket: dict[str, Any]) -> dict[str, Any]: ...
    def detail(self, tx: Transaction, review_id: str) -> dict[str, Any]: ...
    def record_diagnostics(
        self, project_id: str, review_id: str, diagnostics: dict[str, Any]
    ) -> None: ...


class ContextService(Protocol):
    def get(self, project_id: str, context_id: str) -> dict[str, Any]: ...
    def validate_references(self, tx: Transaction, context: dict[str, Any]) -> None: ...
