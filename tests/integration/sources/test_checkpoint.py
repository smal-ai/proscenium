from copy import deepcopy

import pytest
from proscenium.api.errors import DomainError
from proscenium.sources.common import git, sha

from .helpers import commit


def command(head=None):
    return dict(
        schema_version="1.0",
        operation_id="checkpoint-test",
        project_id="project-test",
        paths=["scenes/source.txt"],
        message="Owned changes only",
        expected_head=head,
    )


def test_unborn_checkpoint_preserves_original_index(source_services, source_caller, root):
    (root / "scenes/source.txt").write_bytes(b"first authored bytes")
    (root / "assets/unrelated.txt").write_bytes(b"unrelated staged bytes")
    git(root, "add", "assets/unrelated.txt")
    index = (root / ".git/index").read_bytes()
    result = source_services.extension("sources").checkpoint(source_caller, command())
    assert result["state"] == "completed"
    assert not result["replayed"]
    assert result["parent_commit"] is None
    assert result["files"] == [
        {"path": "scenes/source.txt", "sha256": sha(b"first authored bytes")}
    ]
    assert (root / ".git/index").read_bytes() == index
    assert git(root, "ls-tree", "--name-only", "HEAD").decode().splitlines() == ["scenes"]


def test_lost_git_ack_recovers_exact_commit_after_head_advance(
    source_services, source_caller, root, monkeypatch
):
    initial = commit(root)
    (root / "scenes/source.txt").write_bytes(b"checkpoint v2")
    sources = source_services.extension("sources")
    request = command(initial)
    store = source_services.registry.project_store("project-test")
    from proscenium.db.store import Transaction

    original = Transaction.operation

    def lose_ack(tx, scope, identity, body, effect):
        if scope == "sources.checkpoint":
            raise RuntimeError("synthetic process loss after atomic Git refs before SQLite receipt")
        return original(tx, scope, identity, body, effect)

    with monkeypatch.context() as patch:
        patch.setattr(Transaction, "operation", lose_ack)
        with pytest.raises(RuntimeError):
            sources.checkpoint(source_caller, request)
    checkpoint_commit = git(root, "rev-parse", "HEAD").decode().strip()
    git(root, "read-tree", "HEAD")
    commit(root, "brief/later.txt", b"later unrelated commit")
    current = git(root, "rev-parse", "HEAD").decode().strip()
    source_services.gate.close("project-test")
    result = sources.checkpoint(source_caller, request)
    assert result["replayed"]
    assert result["source_commit"] == checkpoint_commit
    assert git(root, "rev-parse", "HEAD").decode().strip() == current
    with store.transaction(write=False) as tx:
        assert tx.require("checkpoint_intents", request["operation_id"])["state"] == "completed"
    changed = deepcopy(request)
    changed["message"] = "cannot recycle identity"
    with pytest.raises(DomainError) as error:
        sources.checkpoint(source_caller, changed)
    assert error.value.code == "idempotency_conflict"


def test_changed_head_or_unsafe_paths_are_rejected(source_services, source_caller, root):
    initial = commit(root)
    request = command()
    with pytest.raises(DomainError) as error:
        source_services.extension("sources").checkpoint(source_caller, request)
    assert error.value.code == "source_conflict"
    request = command(initial)
    request["operation_id"] = "checkpoint-unsafe"
    request["paths"] = ["../escape.txt"]
    with pytest.raises(DomainError):
        source_services.extension("sources").checkpoint(source_caller, request)


def test_checkpoint_lfs_contains_pointer_and_receipt_hydrated_hash(
    source_services, source_caller, root
):
    # Actual local Git LFS clean filter, not a hand-written synthetic pointer.
    data = b"Blender-v304" + bytes(range(256)) * 30
    (root / "scenes/source.blend").write_bytes(data)
    request = command()
    request["paths"] = ["scenes/source.blend", ".gitattributes"]
    result = source_services.extension("sources").checkpoint(source_caller, request)
    assert git(root, "show", result["source_commit"] + ":scenes/source.blend").startswith(
        b"version https://git-lfs.github.com/spec/v1"
    )
    assert {row["path"]: row["sha256"] for row in result["files"]}["scenes/source.blend"] == sha(
        data
    )


def test_uncertain_ref_effect_is_fenced_until_exact_ref_is_observed(
    source_services, source_caller, root, monkeypatch
):
    import proscenium.sources.checkpoint as module

    (root / "scenes/source.txt").write_bytes(b"bounded uncertain source")
    original = module.git
    launches = []

    def uncertain(project_root, *args, **kwargs):
        if args[0] == "update-ref":
            launches.append(args)
            raise DomainError("source_unavailable", 422)
        return original(project_root, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(module, "git", uncertain)
        receipt = source_services.extension("sources").checkpoint(source_caller, command())
        assert receipt["state"] == "outcome_uncertain" and receipt["source_commit"] is None
        retry = source_services.extension("sources").checkpoint(source_caller, command())
        assert retry["state"] == "outcome_uncertain" and retry["replayed"]
    assert len(launches) == 1
    with source_services.registry.project_store("project-test").transaction(write=False) as tx:
        intent = tx.require("checkpoint_intents", "checkpoint-test")
    reference = "refs/proscenium/checkpoints/" + sha(b"checkpoint-test")
    commands = f"start\nupdate {intent['branch']} {intent['candidate']} {'0' * 40}\ncreate {reference} {intent['candidate']}\nprepare\ncommit\n"
    git(root, "update-ref", "--stdin", data=commands.encode())
    result = source_services.extension("sources").checkpoint(source_caller, command())
    assert result["state"] == "completed" and result["replayed"]
    assert result["source_commit"] == intent["candidate"]
