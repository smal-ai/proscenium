"""Integration-owned composition of accepted feature packages."""

from fastapi import APIRouter
from proscenium.catalogue import install as install_catalogue
from proscenium.catalogue import projects_router
from proscenium.catalogue import router as catalogue_router
from proscenium.reviews import install as install_reviews
from proscenium.reviews import router as reviews_router
from proscenium.services import Services


def feature_routers() -> tuple[APIRouter, ...]:
    return (projects_router, catalogue_router, reviews_router)


def configure_services(services: Services) -> None:
    if not any(
        name in services.extensions for name in ("catalogue", "artifacts", "sources", "projects")
    ):
        install_catalogue(services)
    install_reviews(services)
