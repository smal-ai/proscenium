"""Explicit parent composition. Installing starts no native process or watcher."""

from proscenium.services import Services

from .service import Workflows


def install(services: Services) -> Workflows:
    current = services.extensions.get("workflows")
    if current is not None:
        if not isinstance(current, Workflows):
            raise ValueError("workflows extension is already registered")
        return current
    workflows = Workflows(services)
    services.extensions["workflows"] = workflows

    # F00's cancellation admission checks registration. Parent owns outbox dispatch.
    def cancel(job: dict) -> None:
        workflows.cancel_job(job)

    services.jobs.cancellers.setdefault("unity", cancel)
    return workflows


__all__ = ["Workflows", "install"]
