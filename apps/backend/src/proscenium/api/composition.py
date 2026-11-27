"""Integration-owned composition of accepted feature packages."""

from typing import Any

from fastapi import APIRouter
from proscenium.catalogue import install as install_catalogue
from proscenium.catalogue import projects_router
from proscenium.catalogue import router as catalogue_router
from proscenium.copilot import install as install_copilot
from proscenium.copilot import router as copilot_router
from proscenium.previews import install as install_previews
from proscenium.previews import router as previews_router
from proscenium.providers import install as install_providers
from proscenium.providers import register_provider_artifact
from proscenium.providers import router as providers_router
from proscenium.providers.preparation import (
    AudioPreparation,
    MeshPreparation,
    Preparation,
    VideoPreparation,
)
from proscenium.reviews import install as install_reviews
from proscenium.reviews import router as reviews_router
from proscenium.services import Services
from proscenium.workflows import install as install_workflows


def feature_routers() -> tuple[APIRouter, ...]:
    return (
        projects_router,
        catalogue_router,
        reviews_router,
        copilot_router,
        previews_router,
        providers_router,
    )


class OwnerArtifacts:
    """Keep the entire synchronous owner ingestion inside W02's rebind/copy barrier."""

    def __init__(self, services: Services, delegate: Any):
        self.services, self.delegate = services, delegate

    def __getattr__(self, name: str) -> Any:
        return getattr(self.delegate, name)

    def upload(self, *args: Any, **kwargs: Any) -> Any:
        with self.services.activation_lock:
            return self.delegate.upload(*args, **kwargs)


class GenerationAlias:
    def __init__(self, providers: Any):
        self.providers = providers

    def submit(self, caller: Any, body: dict) -> Any:
        return self.providers.submit(caller, body)


class ReviewAlias:
    def __init__(self, providers: Any):
        self.providers = providers

    def request(self, caller: Any, body: dict) -> Any:
        return self.providers.request_review(caller, body)

    def escalate(self, caller: Any, body: dict) -> Any:
        return self.providers.reviews.escalate(caller, body)


def configure_services(services: Services) -> None:
    if not any(
        name in services.extensions for name in ("catalogue", "artifacts", "sources", "projects")
    ):
        install_catalogue(services)
    install_reviews(services)
    artifacts = services.extensions.get("artifacts")
    if artifacts is not None and not isinstance(artifacts, OwnerArtifacts):
        services.extensions["artifacts"] = OwnerArtifacts(services, artifacts)
    providers = install_providers(
        services, registrar=lambda caller, body: register_provider_artifact(services, caller, body)
    )
    install_previews(services)
    install_workflows(services)
    if services.runtime is None and "contexts" not in services.extensions:
        install_copilot(services)
    services.extensions.setdefault("generation", GenerationAlias(providers))
    services.extensions.setdefault("vlm", ReviewAlias(providers))
    services.extensions.setdefault("image_preparation", Preparation(services, providers))
    services.extensions.setdefault("audio_preparation", AudioPreparation(services, providers))
    services.extensions.setdefault("video_preparation", VideoPreparation(services, providers))
    services.extensions.setdefault("mesh_preparation", MeshPreparation(services, providers))
