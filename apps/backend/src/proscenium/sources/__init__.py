from typing import Any

from proscenium.auth import Caller
from proscenium.services import Services
from proscenium.sources.checkpoint import Checkpoints
from proscenium.sources.resolver import Resolver, closure_digest

__all__ = ["Sources", "closure_digest"]


class Sources(Resolver):
    def __init__(self, services: Services):
        super().__init__(services)
        self.checkpoints = Checkpoints(services, self)

    def checkpoint(self, caller: Caller, request: dict[str, Any]) -> dict[str, Any]:
        return self.checkpoints.checkpoint(caller, request)
