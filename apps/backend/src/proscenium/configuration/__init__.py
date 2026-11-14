import json
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from proscenium.contracts import validate


@dataclass(frozen=True)
class Settings:
    data_directory: Path = field(
        default_factory=lambda: (
            Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "proscenium"
        )
    )
    origin: str = "http://127.0.0.1:8765"
    generation_attempt_limit: int = 3
    execution_profiles: frozenset[str] = frozenset()
    profiles: tuple[dict[str, Any], ...] = ()
    services: tuple[dict[str, Any], ...] = ()
    free_space_reserve_bytes: int = 5 * 1024**3
    frontend_directory: Path | None = None
    revision: int = 0

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        path = (
            path
            or Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
            / "proscenium/config.json"
        )
        if not path.exists():
            return cls()
        body = json.loads(path.read_text())
        if not isinstance(body, dict) or set(body) - {f.name for f in fields(cls)}:
            raise ValueError("Unknown configuration fields")
        for key in ("data_directory", "frontend_directory"):
            if body.get(key) is not None:
                body[key] = Path(body[key]).expanduser()
        if "execution_profiles" in body:
            body["execution_profiles"] = frozenset(body["execution_profiles"])
        for key in ("profiles", "services"):
            if key in body:
                body[key] = tuple(body[key])
        return cls(**body)

    def __post_init__(self) -> None:
        url = urlsplit(self.origin)
        if (
            url.scheme not in {"http", "https"}
            or url.hostname not in {"localhost", "127.0.0.1", "::1"}
            or url.path
            or url.query
            or url.fragment
            or url.username
            or url.password
        ):
            raise ValueError("An exact loopback origin is required")
        if (
            self.generation_attempt_limit < 1
            or self.free_space_reserve_bytes < 0
            or self.revision < 0
        ):
            raise ValueError("Invalid configured limit")
        for service in self.services:
            endpoint = urlsplit(service["endpoint"])
            if endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
                raise ValueError("Service views must not contain credentials or query tokens")
        validate("ConfigurationView", self.view())

    @property
    def host(self) -> str:
        return urlsplit(self.origin).netloc

    def view(self) -> dict[str, Any]:
        return dict(
            schema_version="1.0",
            revision=self.revision,
            generation_attempt_limit=self.generation_attempt_limit,
            profiles=list(self.profiles),
            services=list(self.services),
        )
