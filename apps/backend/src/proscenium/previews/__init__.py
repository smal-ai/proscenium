from proscenium.services import Services

from .router import router
from .service import PreviewService

__all__ = ["PreviewService", "install", "router"]


def install(services: Services) -> PreviewService:
    previous = services.extensions.get("previews")
    if previous is not None:
        return previous
    previews = PreviewService(services)
    services.extensions["previews"] = previews
    from .runner import cancel

    def cancel_render(job: dict) -> None:
        cancel(previews, job["project_id"], job)

    services.jobs.cancellers["render"] = cancel_render
    services.jobs.cancellers["encode"] = cancel_render
    services.jobs.retry_eligibility["render"] = previews.can_retry_cross_work
    return previews
