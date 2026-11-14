"""Pinned native file-IO semantics. Path/data validation, not an OS sandbox."""

from pathlib import Path
from typing import Any

from copilot.generated.session_events import PermissionRequestRead, PermissionRequestWrite

from proscenium.api.errors import DomainError
from proscenium.db.paths import project_path


def file_io(root: Path, request: dict[str, Any]) -> tuple[list[Path], bool]:
    """Validate every lexical/resolved target; return canonical paths and special approval need."""
    kind = request.get("kind")
    try:
        if kind == "read":
            parsed = PermissionRequestRead.from_dict(request).to_dict()
            primary = parsed["path"]
        elif kind == "write":
            parsed = PermissionRequestWrite.from_dict(request).to_dict()
            primary = parsed["fileName"]
        else:
            raise DomainError("invalid_request")
    except (AssertionError, TypeError, ValueError, KeyError) as exc:
        raise DomainError("invalid_request") from exc
    try:
        root = root.resolve(strict=True)
    except (ValueError, OSError, RuntimeError) as exc:
        raise DomainError("scope_denied", 403) from exc

    def target(value: Any) -> Path:
        if not isinstance(value, str) or not value:
            raise DomainError("scope_denied", 403)
        try:
            candidate = Path(value)
            candidate = candidate if candidate.is_absolute() else root / candidate
            # Keep the lexical path: resolving first would hide a forbidden symlink/.. prefix.
            relative = candidate.relative_to(root).as_posix()
            if kind == "write" and candidate.is_dir():
                raise DomainError("scope_denied", 403)
            return project_path(
                root,
                relative,
                managed=kind == "read",
                directory=candidate.is_dir() if kind == "read" else False,
                must_exist=False,
            )
        except (ValueError, OSError, RuntimeError) as exc:
            raise DomainError("scope_denied", 403) from exc

    paths = [target(primary)]
    if parsed.get("resolvedPath") is not None and target(parsed["resolvedPath"]) != paths[0]:
        raise DomainError("scope_denied", 403)
    for edit in parsed.get("fileEdits") or []:
        for side in ("before", "after"):
            if edit.get(side) is not None:
                paths.append(target(edit[side]["path"]))
    special = bool(
        parsed.get("managedApprovalRequired")
        or parsed.get("requestSandboxBypass")
        or parsed.get("sandboxPathGrant")
    )
    return paths, special
