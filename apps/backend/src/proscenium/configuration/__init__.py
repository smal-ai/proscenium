import json
import os
import re
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from proscenium.contracts import validate

APP_ROOT = Path(__file__).resolve().parents[5]


def config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def default_config_path() -> Path:
    return config_home() / "proscenium/config.json"


def _read_dotenv(path: Path) -> dict[str, str]:
    """Read bounded private values without exporting or logging them."""
    try:
        if os.path.islink(path) or not path.is_file() or path.stat().st_size > 65536:
            return {}
        values = {}
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            key, separator, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                continue
            if len(value) >= 2 and value[0] in {"'", '"'} and value[-1] == value[0]:
                value = value[1:-1]
            values[key] = value
        return values
    except (OSError, UnicodeError):
        return {}


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
    runtime: dict[str, Any] = field(default_factory=dict)
    rendering: dict[str, Any] = field(default_factory=dict)
    provider_options: dict[str, Any] = field(default_factory=dict)
    upload_max_bytes: int = 25 * 1024**2
    upload_max_pixels: int = 40_000_000
    attachment_max_count: int = 8
    attachment_max_bytes: int = 100 * 1024**2
    _config_dir: Path | None = field(default=None, repr=False, compare=False)

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        path = Path(path) if path is not None else default_config_path()
        if not path.exists():
            return cls()
        return cls._from_json_text(
            path.read_text(encoding="utf-8"), config_dir=path.resolve().parent
        )

    @classmethod
    def _from_json_text(cls, text: str, *, config_dir: Path) -> "Settings":
        body = json.loads(text)
        allowed_fields = {f.name for f in fields(cls) if not f.name.startswith("_")}
        if not isinstance(body, dict) or set(body) - allowed_fields:
            raise ValueError("Unknown configuration fields")
        substitutions = {
            "HOME": str(Path.home()),
            "CONFIG_DIR": str(config_dir),
            "APP_ROOT": str(APP_ROOT),
        }

        def expand(value: Any) -> Any:
            if isinstance(value, dict):
                return {key: expand(item) for key, item in value.items()}
            if isinstance(value, list):
                return [expand(item) for item in value]
            if isinstance(value, str):
                for key, replacement in substitutions.items():
                    value = value.replace("${" + key + "}", replacement)
                if "${" in value:
                    raise ValueError("Unknown configuration path substitution")
                return str(Path(value).expanduser()) if value.startswith("~/") else value
            return value

        body = expand(body)
        for key in ("data_directory", "frontend_directory"):
            if key not in body or (key == "frontend_directory" and body[key] is None):
                continue
            value = body[key]
            if not isinstance(value, str) or not value or "\0" in value:
                raise ValueError(f"{key} must be a non-empty path string")
            body[key] = Path(value).expanduser()
        if "execution_profiles" in body:
            if not isinstance(body["execution_profiles"], list) or not all(
                isinstance(profile, str) for profile in body["execution_profiles"]
            ):
                raise ValueError("execution_profiles must be an array of strings")
            body["execution_profiles"] = frozenset(body["execution_profiles"])
        for key in ("profiles", "services"):
            if key in body:
                if not isinstance(body[key], list) or not all(
                    isinstance(item, dict) for item in body[key]
                ):
                    raise ValueError(f"{key} must be an array of objects")
                body[key] = tuple(body[key])
        return cls(**body, _config_dir=config_dir)

    def secret_env(self, name: str) -> str | None:
        if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", name):
            raise ValueError("Invalid environment variable name")
        value = os.environ.get(name)
        if value:
            return value
        if self._config_dir is not None:
            for path in (self._config_dir / ".env", APP_ROOT / ".env"):
                value = _read_dotenv(path).get(name)
                if value:
                    return value
        return None

    def __post_init__(self) -> None:
        if not isinstance(self.data_directory, Path) or "\0" in str(self.data_directory):
            raise ValueError("data_directory must be a path")
        if self.frontend_directory is not None and (
            not isinstance(self.frontend_directory, Path) or "\0" in str(self.frontend_directory)
        ):
            raise ValueError("frontend_directory must be a path or null")
        if not isinstance(self.origin, str) or any(c.isspace() or ord(c) < 32 for c in self.origin):
            raise ValueError("An exact loopback origin is required")
        url = urlsplit(self.origin)
        if (
            url.scheme not in {"http", "https"}
            or url.hostname not in {"localhost", "127.0.0.1", "::1"}
            or url.path
            or url.query
            or url.fragment
            or url.username is not None
            or url.password is not None
            or url.port == 0
        ):
            raise ValueError("An exact loopback origin is required")
        for value, lower in (
            (self.generation_attempt_limit, 1),
            (self.free_space_reserve_bytes, 0),
            (self.revision, 0),
            (self.upload_max_bytes, 1),
            (self.upload_max_pixels, 1),
            (self.attachment_max_count, 1),
            (self.attachment_max_bytes, 1),
        ):
            if type(value) is not int or value < lower:
                raise ValueError("Invalid configured limit")
        if not all(
            isinstance(value, dict)
            for value in (self.runtime, self.rendering, self.provider_options)
        ):
            raise ValueError(
                "Private runtime, rendering and provider configuration must be objects"
            )
        for service in self.services:
            if not isinstance(service, dict) or not isinstance(service.get("endpoint"), str):
                raise ValueError("Each service requires a string endpoint")
            endpoint = urlsplit(service["endpoint"])
            if endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
                raise ValueError("Service views must not contain credentials or query tokens")
        validate("ConfigurationView", self.view())
        from proscenium.providers.preparation import validate_recipes

        validate_recipes(self.provider_options)

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
