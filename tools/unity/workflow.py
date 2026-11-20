"""Prepare portable Unity tooling. Native execution is available only through run_unity_job."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from proscenium.api.errors import DomainError
from proscenium.contracts import validate
from proscenium.db.paths import project_path

from .worker import check_project_tree

HERE = Path(__file__).resolve().parent
EDITOR_VERSION = "6000.3.27f1"
PACKAGE_VERSIONS = {
    "com.unity.inputsystem": "1.20.1",
    "com.unity.render-pipelines.universal": "17.3.0",
    "com.unity.test-framework": "1.6.0",
    "com.unity.modules.physics": "1.0.0",
    "com.unity.modules.jsonserialize": "1.0.0",
}


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def scoped(root: Path, relative: str, *, directory: bool = False) -> Path:
    if any(part in {"", ".", ".."} for part in relative.split("/")):
        raise DomainError("scope_denied", 403)
    return project_path(root, relative, must_exist=False, directory=directory)


def unity_path(root: Path, relative: str) -> Path:
    if not relative.startswith("unity/"):
        raise DomainError("scope_denied", 403, "Unity projects must live beneath unity/")
    return scoped(root, relative, directory=True)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def prepare(root: Path, project: str, *, tests: bool = False) -> Path:
    """Install only tool-owned scripts, never overwrite a user script or delete a .meta."""
    from .worker import editor_running

    destination = unity_path(root, project)
    if editor_running(destination):
        raise DomainError(
            "project_busy", 409, "Save/close this project's Editor before preparing scripts"
        )
    destination.mkdir(parents=True, exist_ok=True)
    inventory = scoped(root, f"{project}/Assets/Proscenium/tool-install.json")
    installed = json.loads(inventory.read_text()) if inventory.is_file() else {}
    sources = [HERE / "Runtime", HERE / "Editor"]
    if tests:
        sources.append(HERE / "Tests")
    updates = []
    for source in sources:
        for path in source.rglob("*"):
            if not path.is_file():
                continue
            target = scoped(root, f"{project}/Assets/Proscenium/{path.relative_to(HERE)}")
            name = path.relative_to(HERE).as_posix()
            if target.is_file() and sha256(target) not in {installed.get(name), sha256(path)}:
                raise DomainError(
                    "source_conflict", 409, "Preserve locally edited Unity tool script: " + name
                )
            updates.append((path, target, name))
    for path, target, name in updates:
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() or path.read_bytes() != target.read_bytes():
            shutil.copyfile(path, target)
        installed[name] = sha256(path)
    write_json(inventory, installed)
    return destination


def verify_materialisation(root: Path, closure: dict[str, Any]) -> Path:
    validate("SourceClosure", closure)
    for row in closure["files"]:
        path = project_path(root, row["destination"])
        if sha256(path) != row["origin"]["sha256"]:
            raise DomainError("source_conflict", 409, "Materialised source hash differs")
    entry = project_path(root, closure["entry_path"])
    if entry.suffix.lower() != ".blend":
        raise DomainError("invalid_request", message="Expected a pinned Blender source")
    return entry


def bootstrap_project(root: Path, project: str, *, tests: bool = False) -> Path:
    """Minimal fresh project scaffold. Actual settings/scene/assets are saved by the Editor.

    No existing package/settings files are overwritten. Package installation remains a
    host-authorised prerequisite, not permission inferred from a model-written recipe.
    """
    destination = unity_path(root, project)
    if (destination / "ProjectSettings").exists() or (destination / "Packages").exists():
        raise DomainError(
            "source_conflict", 409, "Existing Unity project: use prepare, not bootstrap"
        )
    destination = prepare(root, project, tests=tests)
    packages = dict(PACKAGE_VERSIONS)
    if not tests:
        packages.pop("com.unity.test-framework")
    write_json(scoped(root, f"{project}/Packages/manifest.json"), {"dependencies": packages})
    settings = scoped(root, f"{project}/ProjectSettings/ProjectVersion.txt")
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(f"m_EditorVersion: {EDITOR_VERSION}\n")
    return destination


def source_key(source_id: str) -> str:
    # Opaque source IDs never become paths or C# object selectors directly.
    return hashlib.sha256(source_id.encode()).hexdigest()[:24]


def existing_recipe(root: Path, body: dict[str, Any], version: dict[str, Any]) -> dict[str, Any]:
    """Freeze an authored project's batch entry point without installing tooling."""
    project = unity_path(root, body["project"])
    if project != root / body["project"]:
        raise DomainError("scope_denied", 403, "Authored Unity project cannot be a symlink")
    try:
        check_project_tree(project)
    except (OSError, RuntimeError) as error:
        raise DomainError(
            "scope_denied", 403, f"Cannot inspect authored Unity project: {error}"
        ) from error

    def scene_path(value: str) -> str:
        candidate = value.removeprefix(body["project"] + "/")
        if not candidate.startswith("Assets/") or not candidate.endswith(".unity"):
            raise DomainError("scope_denied", 403, "Scene must be inside this project's Assets")
        scene = scoped(project, candidate)
        if scene != project / candidate or not scene.is_file():
            raise DomainError("source_unavailable", 422, "Unity scene is missing")
        return scene.relative_to(root).as_posix()

    if body["action"] not in {"validate", "build"}:
        raise DomainError("invalid_request", 422, "Authored Unity projects support validate/build")
    settings = scoped(project, "ProjectSettings/ProjectVersion.txt")
    if settings != project / "ProjectSettings/ProjectVersion.txt" or not settings.is_file():
        raise DomainError("source_unavailable", 422, "ProjectVersion.txt is missing")
    version_match = re.search(r"^m_EditorVersion:\s*(\S+)", settings.read_text(), re.MULTILINE)
    if not version_match:
        raise DomainError("invalid_request", 422, "ProjectVersion.txt has no editor version")
    editor_version = version_match[1]
    adapter_path = scoped(project, "proscenium-unity.json")
    if adapter_path != project / "proscenium-unity.json":
        raise DomainError("scope_denied", 403, "Unity adapter cannot be a symlink")
    adapter: dict[str, Any] = {}
    if adapter_path.exists():
        try:
            adapter = json.loads(adapter_path.read_text())
        except (ValueError, OSError) as exc:
            raise DomainError("invalid_request", 422, "Invalid Unity adapter JSON") from exc
        if (
            not isinstance(adapter, dict)
            or set(adapter)
            - {
                "editor_version",
                "pipeline",
                "scene_path",
                "validate_method",
                "build_method",
                "build_output",
            }
            or any(not isinstance(value, str) or not value.strip() for value in adapter.values())
        ):
            raise DomainError("invalid_request", 422, "Invalid Unity adapter fields")
    pinned = version["unity"]
    scene = scene_path(body.get("scene_path", pinned["scene_path"]))
    if (
        scene != pinned["scene_path"]
        or scene_path(adapter.get("scene_path", scene)) != scene
        or adapter.get("editor_version", editor_version) != editor_version
        or pinned["editor_version"] != editor_version
        or adapter.get("pipeline", pinned["pipeline"]) != pinned["pipeline"]
        or body["pipeline"] != pinned["pipeline"]
    ):
        raise DomainError("source_conflict", 409, "Unity adapter differs from the pinned project")
    method = adapter.get(body["action"] + "_method")
    if method and not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+", method, re.ASCII):
        raise DomainError("invalid_request", 422, "Invalid Unity batch method")
    output = adapter.get("build_output") if body["action"] == "build" else None
    if body["action"] == "build" and (not method or not output):
        raise DomainError("capability_unavailable", 501, "Build needs a method and output path")
    if output and scoped(project, output) != project / output:
        raise DomainError("scope_denied", 403, "Unity build output cannot be a symlink")
    if pinned.get("player_script"):
        script = scoped(root, pinned["player_script"])
        if not script.is_relative_to(project) or not script.is_file():
            raise DomainError("source_unavailable", 422, "Unity player script is missing")
    return dict(
        existing=True,
        source_version=body["version"],
        source=version["source"],
        unity=pinned,
        method=method,
        build_output=output,
    )


def make_recipe(
    root: Path,
    project: str,
    *,
    source_id: str,
    source_version: dict[str, Any],
    closure: dict[str, Any],
    export_path: str,
    spawn: tuple[float, float, float],
    scene_path: str | None = None,
    camera: str = "perspective",
    pipeline: str = "builtin",
) -> dict[str, Any]:
    unity_path(root, project)
    validate("VersionRef", source_version)
    validate("SourceClosure", closure)
    export = scoped(root, export_path)
    if camera not in {"perspective", "orthographic"} or pipeline not in {"builtin", "urp"}:
        raise DomainError("invalid_request")
    key = source_key(source_id)
    scene = scene_path or f"Assets/Proscenium/Generated/{key}/Playable.unity"
    if not re.fullmatch(r"Assets/(?:[A-Za-z0-9_-]+/)+[A-Za-z0-9_-]+\.unity", scene):
        raise DomainError("scope_denied", 403)
    scoped(root, f"{project}/{scene}")
    return dict(
        source_id=source_id,
        source_key=key,
        source_version=source_version,
        source=closure,
        export_path=export_path,
        export_sha256=sha256(export),
        scene_path=scene,
        spawn=dict(zip(("x", "y", "z"), spawn, strict=True)),
        camera=camera,
        pipeline=pipeline,
    )


def input_digest(recipe: dict[str, Any]) -> str:
    """Tool recipe identity, not a replacement for SourceClosure's RFC8785 digest."""
    return hashlib.sha256(
        json.dumps(recipe, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "bootstrap", "inspect"])
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--tests", action="store_true")
    args = parser.parse_args()
    if args.action == "prepare":
        print(prepare(args.root.resolve(strict=True), args.project, tests=args.tests))
    elif args.action == "bootstrap":
        print(bootstrap_project(args.root.resolve(strict=True), args.project, tests=args.tests))
    else:
        project = unity_path(args.root, args.project)
        print(json.dumps({"project": str(project), "exists": project.is_dir()}))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, DomainError) as error:
        raise SystemExit(str(error)) from error
