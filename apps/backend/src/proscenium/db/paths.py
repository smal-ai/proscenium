from pathlib import Path, PurePosixPath

from proscenium.api.errors import DomainError

DENIED_PARTS = {
    ".git",
    ".direnv",
    ".ssh",
    ".aws",
    ".azure",
    ".config",
    "credentials",
    "credential",
    "secrets",
    "tokens",
    "id_rsa",
    "id_ed25519",
}
MANAGED_ROOTS = {"artefacts", "retained", "snapshots", "jobs", "tool-output"}


def project_path(
    root: Path,
    relative: str,
    *,
    managed: bool = False,
    directory: bool = False,
    must_exist: bool = True,
) -> Path:
    """Validate lexical AND resolved paths. managed is trusted service policy, never wire input."""
    path = PurePosixPath(relative)
    if (
        not relative
        or ":" in relative
        or "\\" in relative
        or "\x00" in relative
        or path.is_absolute()
    ):
        raise DomainError("scope_denied", 403)
    if ".." in path.parts or (relative == "." and not directory):
        raise DomainError("scope_denied", 403)
    canonical_root = root.resolve(strict=True)
    try:
        resolved = (canonical_root / relative).resolve(strict=must_exist)
        actual = resolved.relative_to(canonical_root)
    except (ValueError, OSError, RuntimeError) as exc:
        raise DomainError("scope_denied", 403) from exc
    for parts in (path.parts, actual.parts):
        if ".proscenium" in parts:
            index = parts.index(".proscenium")
            if not managed or len(parts) <= index + 1 or parts[index + 1] not in MANAGED_ROOTS:
                raise DomainError("scope_denied", 403)
        for part in parts:
            lower = part.lower()
            if (
                lower in DENIED_PARTS
                or lower.startswith(".env")
                or lower.endswith((".pem", ".key"))
                or (lower == ".proscenium" and not managed)
            ):
                raise DomainError("scope_denied", 403)
    if must_exist and (resolved.is_dir() != directory):
        raise DomainError("invalid_request", message="Unexpected file/directory kind")
    return resolved
