from proscenium.api.errors import DomainError


class ExecutionGate:
    """Closed on every process boot. W01 opens only after explicit Resume and reconciliation."""

    def __init__(self) -> None:
        self._open: set[str] = set()

    def require(self, project_id: str) -> None:
        if project_id not in self._open:
            raise DomainError(
                "project_busy", 409, "Execution awaits explicit Resume", safe_for_model=True
            )

    def resume(self, project_id: str, *, reconciliation_evidence: str) -> None:
        if not reconciliation_evidence:
            raise DomainError("attribution_unavailable", 409)
        self._open.add(project_id)

    def close(self, project_id: str) -> None:
        self._open.discard(project_id)
