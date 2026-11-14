"""W07 local providers. Import/installation never executes a model or manages a service."""

from proscenium.providers.bridge import register_provider_artifact
from proscenium.providers.router import router
from proscenium.providers.service import (
    ProviderService,
    bind_generation_task,
    extend_generation_task,
    install,
)

__all__ = [
    "ProviderService",
    "bind_generation_task",
    "extend_generation_task",
    "install",
    "router",
    "register_provider_artifact",
]
