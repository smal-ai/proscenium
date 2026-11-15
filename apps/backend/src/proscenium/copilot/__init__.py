"""Explicit parent composition: install(services), router, await start()/close()."""

from .router import router
from .service import Copilot, install

__all__ = ["Copilot", "install", "router"]
