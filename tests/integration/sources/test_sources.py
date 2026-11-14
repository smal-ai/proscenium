from copy import deepcopy

import pytest
from proscenium.api.errors import DomainError
from proscenium.sources import closure_digest
from proscenium.sources.common import sha

from .helpers import closure, commit


def test_historical_materialisation_copies_committed_bytes(source_services, root):
    revision = commit(root)
    manifest = closure(revision)
    (root / "scenes/source.txt").write_bytes(b"uncommitted future bytes")
    materialised = source_services.extension("sources").resolve("project-test", manifest)
    pinned = materialised / manifest["entry_path"]
    assert pinned.read_bytes() == b"authored v1\n"
    assert pinned.stat().st_ino != (root / "scenes/source.txt").stat().st_ino
    assert pinned.stat().st_mode & 0o222 == 0
    assert (materialised / ".receipt.json").is_file()


def test_digest_order_and_collision(source_services, root):
    revision = commit(root)
    manifest = closure(revision)
    other = deepcopy(manifest["files"][0])
    other["destination"] = "scenes/copy.txt"
    manifest["files"].append(other)
    manifest["digest"] = closure_digest(manifest)
    manifest["files"].reverse()
    assert closure_digest(manifest) == manifest["digest"]
    source_services.extension("sources").resolve("project-test", manifest)
    other_commit = commit(root, content=b"different bytes")
    conflicting = closure(other_commit, content=b"different bytes")["files"][0]
    manifest["files"].append(conflicting)
    manifest["digest"] = closure_digest(manifest)
    with pytest.raises(DomainError) as error:
        source_services.extension("sources").resolve("project-test", manifest)
    assert error.value.code == "dependency_collision"


@pytest.mark.parametrize(
    "path",
    [
        "../outside",
        "/etc/passwd",
        ".env",
        ".proscenium/state.sqlite3",
        "scenes//bad",
        "scenes/./bad",
        ".npmrc",
        "Library/cache.bin",
        "node_modules/asset.bin",
        ".receipt.json/file",
    ],
)
def test_unsafe_destination_rejected(source_services, root, path):
    manifest = closure(commit(root))
    manifest["files"][0]["destination"] = path
    manifest["digest"] = closure_digest(manifest)
    with pytest.raises(DomainError):
        source_services.extension("sources").resolve("project-test", manifest)


def test_missing_lfs_is_not_fetched(source_services, root):
    data = b"local only LFS bytes" * 100
    revision = commit(root, "assets/image.png", data)
    manifest = closure(revision, "assets/image.png", data)
    sources = source_services.extension("sources")
    assert (sources.resolve("project-test", manifest) / "assets/image.png").read_bytes() == data
    digest = sha(data)
    (root / ".git/lfs/objects" / digest[:2] / digest[2:4] / digest).unlink()
    with pytest.raises(DomainError) as error:
        sources.resolve("project-test", manifest)
    assert error.value.code == "source_unavailable"


def test_missing_retained_and_symlink(source_services, root):
    manifest = closure(commit(root))
    manifest["files"][0]["origin"] = dict(
        kind="retained", artifact_id="retained-test", sha256="a" * 64
    )
    manifest["digest"] = closure_digest(manifest)
    with pytest.raises(DomainError) as error:
        source_services.extension("sources").resolve("project-test", manifest)
    assert error.value.code == "retained_source_missing"
    (root / "scenes/link.txt").symlink_to(root / "scenes/source.txt")
    manifest = closure(commit(root, "brief/file.txt", b"fixture"), "brief/file.txt", b"fixture")
    manifest["files"][0]["destination"] = "scenes/link.txt"
    manifest["digest"] = closure_digest(manifest)
    with pytest.raises(DomainError) as error:
        source_services.extension("sources").resolve("project-test", manifest)
    assert error.value.code == "scope_denied"
