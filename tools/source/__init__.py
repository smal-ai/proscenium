"""SDK tool bindings use the host-admitted Caller, never model attribution."""

from typing import Any

from proscenium.auth import Caller
from proscenium.services import Services


def checkpoint_sources(
    services: Services, caller: Caller, request: dict[str, Any]
) -> dict[str, Any]:
    return dict(services.extension("sources").checkpoint(caller, request))


def publish_outputs(services: Services, caller: Caller, request: dict[str, Any]) -> dict[str, Any]:
    result, _ = services.extension("catalogue").publish(caller, request)
    return dict(result)
