"""Parent composition: install(services), then include both exported routers."""

from proscenium.catalogue.artifacts import Artifacts, ImageLimits
from proscenium.catalogue.router import router
from proscenium.catalogue.service import Catalogue
from proscenium.projects import Projects
from proscenium.projects import router as projects_router
from proscenium.services import Services
from proscenium.sources import Sources

__all__ = ["Artifacts", "Catalogue", "ImageLimits", "install", "router", "projects_router"]


def install(services: Services, *, limits: ImageLimits | None = None) -> Catalogue:
    if any(key in services.extensions for key in ("sources", "artifacts", "catalogue", "projects")):
        # Repeated composition is harmless, but never replace another package's implementation.
        current = services.extensions.get("catalogue")
        if isinstance(current, Catalogue):
            return current
        raise ValueError("W02 service names are already registered")
    artifacts = Artifacts(services, limits)
    sources = Sources(services)
    catalogue = Catalogue(services, sources, artifacts)
    services.extensions.update(
        artifacts=artifacts, sources=sources, catalogue=catalogue, projects=Projects(services)
    )
    return catalogue
