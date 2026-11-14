"""Integration-owned composition of accepted feature packages."""

from fastapi import APIRouter
from proscenium.reviews import install as install_reviews
from proscenium.reviews import router as reviews_router
from proscenium.services import Services


def feature_routers() -> tuple[APIRouter, ...]:
    return (reviews_router,)


def configure_services(services: Services) -> None:
    install_reviews(services)
