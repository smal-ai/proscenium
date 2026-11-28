"""Wave 4: pinned composition against disposable Git, LFS and retained stores."""

import copy
import json
import os
import tracemalloc
from time import perf_counter

import pytest
from proscenium.api.errors import DomainError
from proscenium.contracts import canonical
from proscenium.copilot.tools import result_text, tool_failure
from proscenium.sources import checkpoint as checkpoint_module
from proscenium.sources import closure_digest, composition
from proscenium.sources.common import git, sha
from proscenium.sources.read_only import read_closure_files
from test_checkpoint_closure import authoring_validation, retain
from test_checkpoint_closure import offline as offline
from test_checkpoint_closure import runtime as runtime
from test_project_importer import pin, write

HERO = pin("hero", "hero-v3")
WALK = pin("walk")
STAIRS = pin("staircase")
LANDING = pin("landing", "landing-v2")
HERO_BLEND = "assets/hero/source/hero.blend"
COAT3 = "assets/hero/source/coat_v3.txt"
WALK_BLEND = "assets/walk/source/walk.blend"
STAIRS_BLEND = "assets/staircase/source/staircase.blend"
LANDING_BLEND = "scenes/landing/landing.blend"


def lfs(root):
    git(root, "lfs", "install", "--local")
    git(root, "lfs", "track", "*.blend")
    git(root, "add", ".gitattributes")
    git(root, "commit", "-m", "Track Blender source")


def checkpoint_request(r, identity, paths, *, entry=None, **extra):
    return dict(
        schema_version="1.0",
        project_id=r.caller.project_id,
        operation_id=identity,
        paths=list(paths),
        expected_head=git(r.root, "rev-parse", "HEAD").decode().strip(),
        message=identity,
        entry_path=entry or paths[0],
        **extra,
    )


def checkpoint(r, ref, paths, **extra):
    return r.sources.checkpoint(r.caller, checkpoint_request(r, ref["version_id"], paths, **extra))


def submission(r, ref, receipt, *, inline=False, kind="asset", previous=None, name=None, **extra):
    source = (
        {"source": receipt["source_closure"]}
        if inline
        else {"checkpoint_operation_id": receipt["operation_id"]}
    )
    return dict(
        schema_version="1.0",
        project_id=r.caller.project_id,
        publication_id=f"publish-{ref['version_id']}",
        items=[
            dict(
                **ref,
                **source,
                name=name or ref["item_id"],
                kind=kind,
                source_commit=receipt["source_commit"],
                expected_current_version_id=previous,
                artifact_ids=[],
                preview_requests=[],
                **extra,
            )
        ],
    )


def publish(r, ref, receipt, **options):
    return r.catalogue.publish(r.caller, submission(r, ref, receipt, **options))


def producer(r, ref, paths, *, name=None, **extra):
    for path, data in paths.items():
        write(r.root, path, data)
    receipt = checkpoint(r, ref, list(paths), **extra)
    publish(r, ref, receipt, name=name)
    return receipt


def file_rows(closure):
    return {row["destination"]: row for row in closure["files"]}


def test_pinned_lfs_sources_survive_worktree_drift_and_keep_metadata_pins_out(runtime):
    r = runtime
    lfs(r.root)
    coat = b"Hero coat v3"
    hero = producer(r, HERO, {HERO_BLEND: b"Hero v3", COAT3: coat})
    ordinary = pin("ordinary")
    producer(r, ordinary, {"ordinary.txt": b"Ordinary pin is not a file"})
    walk = producer(r, WALK, {WALK_BLEND: b"Walk v1"}, dependencies=[ordinary])
    stairs = producer(r, STAIRS, {STAIRS_BLEND: b"Stairs v1"})
    write(r.root, HERO_BLEND, b"Hero v4 at HEAD")
    git(r.root, "add", HERO_BLEND)
    git(r.root, "commit", "-m", "Author revised Hero")
    (r.root / WALK_BLEND).unlink()
    (r.root / STAIRS_BLEND).unlink()
    write(r.root, LANDING_BLEND, b"Landing v2")
    receipt = checkpoint(
        r,
        LANDING,
        [LANDING_BLEND],
        include_sources=[WALK, STAIRS, HERO],
        dependencies=[HERO, ordinary],
    )
    closure = receipt["source_closure"]
    assert closure["included_sources"] == [HERO, STAIRS, WALK]
    assert closure["dependencies"] == [HERO, ordinary, STAIRS, WALK]
    rows = file_rows(closure)
    for prod in (hero, walk, stairs):
        for path, row in file_rows(prod["source_closure"]).items():
            assert rows[path] == row
    assert "ordinary.txt" not in rows
    assert rows[LANDING_BLEND]["origin"]["commit"] == receipt["source_commit"]
    assert (
        git(r.root, "diff-tree", "--no-commit-id", "--name-only", "-r", receipt["source_commit"])
        == (LANDING_BLEND + "\n").encode()
    )
    assert git(r.root, "diff", "--cached", "--name-only") == b""
    assert (r.root / HERO_BLEND).read_bytes() == b"Hero v4 at HEAD"
    assert not (r.root / WALK_BLEND).exists()
    body = submission(r, LANDING, receipt, inline=True, kind="scene")
    assert authoring_validation(r, body)["valid"]
    r.catalogue.publish(r.caller, body)
    target = r.sources.materialise(r.caller.project_id, LANDING["version_id"])
    assert (target / COAT3).read_bytes() == coat
    assert (target / HERO_BLEND).read_bytes() == b"Hero v3"
    assert (target / WALK_BLEND).read_bytes() == b"Walk v1"
    assert (target / STAIRS_BLEND).read_bytes() == b"Stairs v1"
    assert not (target / "ordinary.txt").exists()


def test_transitive_whole_manifest_shared_rows_retained_and_large_receipt(runtime):
    r = runtime
    deps = pin("referenced")
    producer(r, deps, {"unloaded.txt": b"Don't open"})
    retained = dict(destination="motion/walk.motion", origin=retain(r))
    files = {f"assets/hero/sidecars/part-{i:03}.txt": str(i).encode() for i in range(75)}
    a = producer(r, HERO, files, retained_files=[retained], dependencies=[deps])
    b = producer(r, WALK, {"walk.txt": b"B"}, include_sources=[HERO])
    c = pin("consumer")
    write(r.root, "consumer.txt", b"C")
    receipt = checkpoint(r, c, ["consumer.txt"], include_sources=[WALK, HERO])
    expected = receipt["source_closure"]
    assert len(canonical(receipt)) > 16_384
    assert json.loads(result_text("checkpoint_sources", receipt)) == receipt
    assert len(expected["files"]) == len(a["source_closure"]["files"]) + 2
    rows = file_rows(expected)
    assert rows[retained["destination"]] == retained
    assert all(rows[name] == row for name, row in file_rows(b["source_closure"]).items())
    assert expected["dependencies"] == [HERO, WALK]
    assert "unloaded.txt" not in rows
    assert authoring_validation(r, submission(r, c, receipt))["valid"]
    publish(r, c, receipt)
    assert (
        read_closure_files(r.services, r.caller.project_id, expected)[retained["destination"]]
        == b"Retained fixture"
    )


@pytest.mark.parametrize(
    "fault,code",
    [
        ("two_origins", "dependency_collision"),
        ("local", "dependency_collision"),
        ("retained_local", "dependency_collision"),
        ("base_owned", "dependency_collision"),
        ("prefix", "dependency_collision"),
        ("entry", "dependency_collision"),
        ("exclusion", "dependency_collision"),
        ("wrong_repo", "scope_denied"),
        ("reserved", "scope_denied"),
        ("damaged_retained", "hash_mismatch"),
        ("missing_lfs", "source_unavailable"),
        ("stale_head", "source_conflict"),
        ("include_limit", "invalid_request"),
        ("destination_limit", "invalid_request"),
        ("byte_limit", "invalid_request"),
        ("missing", "dependency_missing"),
    ],
)
def test_invalid_initial_composition_has_no_git_ref_or_intent(runtime, monkeypatch, fault, code):
    r = runtime
    path = "asset.txt"
    retained = retain(r)
    if fault == "missing_lfs":
        lfs(r.root)
        path = HERO_BLEND
    hero = producer(
        r,
        HERO,
        {path: b"same bytes"},
        retained_files=[dict(destination="retained.txt", origin=retained)],
    )
    extra = {"include_sources": [HERO]}
    paths = ["scene.txt"]
    write(r.root, paths[0], b"Scene")
    if fault in {"two_origins", "base_owned"}:
        producer(r, WALK, {path: b"same bytes"})
        if fault == "two_origins":
            extra["include_sources"].append(WALK)
        else:
            extra["base_version"] = WALK
    if fault == "local":
        paths.append(path)
    if fault == "retained_local":
        extra["retained_files"] = [dict(destination=path, origin=retained)]
    if fault == "prefix":
        paths.append(path + "/child.txt")
    if fault == "entry":
        extra["entry"] = path
    if fault == "exclusion":
        base = pin("base")
        producer(r, base, {"base.txt": b"Base"}, include_sources=[HERO])
        extra = dict(base_version=base, exclude_paths=[path])
    if fault in {"wrong_repo", "reserved"}:
        with r.store.transaction() as tx:
            row = tx.require("versions", HERO["version_id"])
            closure = row["source"]
            entry = file_rows(closure)[path]
            if fault == "wrong_repo":
                entry["origin"]["repository_id"] = "foreign"
            else:
                entry["destination"] = ".receipt.json"
            closure["digest"] = closure_digest(closure)
            tx.put(
                "versions",
                HERO["version_id"],
                row,
                expected_revision=tx.revision("versions", HERO["version_id"]),
            )
    if fault == "damaged_retained":
        (r.root / f".proscenium/retained/{retained['sha256']}/content").write_bytes(b"corrupt")
    if fault == "missing_lfs":
        oid = sha(b"same bytes")
        (r.root / f".git/lfs/objects/{oid[:2]}/{oid[2:4]}/{oid}").unlink()
        # A current worktree copy must not substitute for the missing immutable object.
        assert (r.root / path).is_file()
    if fault == "include_limit":
        extra["include_sources"] = [pin(f"asset-{i}") for i in range(129)]
    if fault == "destination_limit":
        monkeypatch.setattr(composition, "MAX_DESTINATIONS", 2)
    if fault == "byte_limit":
        monkeypatch.setattr(composition, "MAX_BYTES", 8)
    if fault == "missing":
        extra["include_sources"] = [WALK]
    body = checkpoint_request(r, "invalid-compose", paths, **extra)
    if fault == "stale_head":
        body["expected_head"] = hero["parent_commit"]
    refs = git(r.root, "show-ref")
    with pytest.raises(DomainError) as caught:
        r.sources.checkpoint(r.caller, body)
    assert caught.value.code == code
    if code == "dependency_collision":
        assert caught.value.status == 409
    if fault == "two_origins":
        error = caught.value
        assert error.details["conflicting_references"] == [HERO, WALK]
        error.details["arbitrary"] = "/private/token"
        model_details = json.loads(tool_failure(error, "checkpoint_sources").text_result_for_llm)[
            "details"
        ]
        assert model_details == dict(
            destination=path, reference=WALK, conflicting_references=[HERO, WALK]
        )
        error.details["conflicting_references"] = [HERO, {**WALK, "version_id": "/private/token"}]
        assert (
            "conflicting_references"
            not in json.loads(tool_failure(error, "checkpoint_sources").text_result_for_llm)[
                "details"
            ]
        )
    assert git(r.root, "show-ref") == refs
    with r.store.transaction(write=False) as tx:
        assert tx.get("checkpoint_intents", body["operation_id"], required=False) is None


@pytest.mark.parametrize("include_sources", [[HERO], []])
def test_composition_rejects_ambiguous_surviving_legacy_base_origins(runtime, include_sources):
    r = runtime
    producer(r, HERO, {"hero.txt": b"Asset"})
    base = pin("legacy-base")
    for path in ("entry.txt", "first.txt", "second.txt", "edit.txt"):
        write(r.root, path, b"Same bytes")
    receipt = checkpoint(r, base, ["entry.txt", "first.txt", "second.txt"])
    closure = receipt["source_closure"]
    for row in closure["files"]:
        if row["destination"] != "entry.txt":
            row["destination"] = "ambiguous.txt"
    closure["digest"] = closure_digest(closure)
    publish(r, base, receipt, inline=True)
    refs = git(r.root, "show-ref")
    body = checkpoint_request(
        r,
        "ambiguous-base",
        ["edit.txt"],
        entry="entry.txt",
        base_version=base,
        include_sources=include_sources,
    )
    with pytest.raises(DomainError) as error:
        r.sources.checkpoint(r.caller, body)
    assert (error.value.code, error.value.status, error.value.details["destination"]) == (
        "dependency_collision",
        409,
        "ambiguous.txt",
    )
    assert git(r.root, "show-ref") == refs
    with r.store.transaction(write=False) as tx:
        assert tx.get("checkpoint_intents", body["operation_id"], required=False) is None
    # Legacy-only inheritance keeps its historical last-row behaviour, and a
    # composition can explicitly retire the ambiguous destination.
    legacy = checkpoint(r, pin("keep-legacy"), ["edit.txt"], entry="entry.txt", base_version=base)
    assert file_rows(legacy["source_closure"])["ambiguous.txt"]["origin"]["path"] == "second.txt"
    retired = checkpoint(
        r,
        pin("retire-ambiguous"),
        ["edit.txt"],
        entry="entry.txt",
        base_version=base,
        exclude_paths=["ambiguous.txt"],
        include_sources=include_sources,
    )
    assert "ambiguous.txt" not in file_rows(retired["source_closure"])


@pytest.mark.parametrize("compose", [False, True])
def test_untracked_binary_refused_before_checkpoint_intent_or_refs(runtime, compose):
    r = runtime
    producer(r, HERO, {"hero.txt": b"Asset"})
    write(r.root, LANDING_BLEND, b"Untracked Blender source")
    # Only the owner's index has an LFS tracking rule. The checkpoint's
    # isolated index, seeded from HEAD, must not take it as evidence.
    write(r.root, ".gitattributes", b"*.blend filter=lfs\n")
    git(r.root, "add", ".gitattributes")
    (r.root / ".gitattributes").unlink()
    refs = git(r.root, "show-ref")
    index = git(r.root, "ls-files", "--stage")
    with r.store.transaction(write=False) as tx:
        intents = tx.all("checkpoint_intents")
    body = checkpoint_request(
        r, "untracked-blend", [LANDING_BLEND], **({"include_sources": [HERO]} if compose else {})
    )
    with pytest.raises(
        DomainError, match="Binary authoring files require Git LFS tracking"
    ) as error:
        r.sources.checkpoint(r.caller, body)
    assert error.value.code == "source_conflict"
    assert git(r.root, "show-ref") == refs
    assert git(r.root, "ls-files", "--stage") == index
    with r.store.transaction(write=False) as tx:
        assert tx.all("checkpoint_intents") == intents


@pytest.mark.parametrize(
    "fault,code",
    [
        ("missing_row", "dependency_missing"),
        ("changed_row", "dependency_missing"),
        ("missing_pin", "dependency_missing"),
        ("missing_version", "dependency_missing"),
        ("included_entry", "dependency_collision"),
        ("old_entry_commit", "source_conflict"),
        ("duplicate_destination", "dependency_collision"),
        ("unsorted_includes", "invalid_request"),
    ],
)
def test_fabricated_inline_manifests_fail_publish_preflight_and_read(runtime, fault, code):
    r = runtime
    hero = producer(r, HERO, {"asset.txt": b"Asset"})
    producer(r, WALK, {"walk.txt": b"Walk"})
    write(r.root, "scene.txt", b"Scene")
    receipt = checkpoint(r, LANDING, ["scene.txt"], include_sources=[HERO, WALK])
    body = submission(r, LANDING, receipt, inline=True, kind="scene")
    closure = body["items"][0]["source"]
    if fault == "missing_row":
        closure["files"] = [row for row in closure["files"] if row["destination"] != "asset.txt"]
    if fault == "changed_row":
        file_rows(closure)["asset.txt"]["origin"]["commit"] = receipt["source_commit"]
    if fault == "missing_pin":
        closure["dependencies"].remove(HERO)
    if fault == "missing_version":
        missing = pin("unknown")
        closure["dependencies"][0] = missing
        closure["included_sources"] = [missing, WALK]
    if fault == "included_entry":
        closure["entry_path"] = "asset.txt"
    if fault == "old_entry_commit":
        body["items"][0]["source_commit"] = hero["source_commit"]
    if fault == "duplicate_destination":
        closure["files"].append(copy.deepcopy(closure["files"][0]))
    if fault == "unsorted_includes":
        closure["included_sources"].reverse()
    closure["digest"] = closure_digest(closure)
    preflight = authoring_validation(r, body)
    assert not preflight["valid"] and preflight["issues"][0]["code"] == code
    with pytest.raises(DomainError) as err:
        r.catalogue.publish(r.caller, body)
    assert err.value.code == code
    if fault != "old_entry_commit":  # A bare closure has no surrounding publication commit.
        with pytest.raises(DomainError) as err:
            read_closure_files(r.services, r.caller.project_id, closure)
        assert err.value.code == code
    with r.store.transaction(write=False) as tx:
        assert tx.get("versions", LANDING["version_id"], required=False) is None


def test_base_owned_lane_legacy_conversion_retention_detachment_replacement(runtime):
    r = runtime
    old_dest = "linked/old.txt"
    producer(r, HERO, {old_dest: b"Pinned old"})
    next_hero = pin("hero", "hero-v4")
    write(r.root, "linked/new.txt", b"New")
    publish(r, next_hero, checkpoint(r, next_hero, ["linked/new.txt"]), previous=HERO["version_id"])
    legacy = pin("old-scene")
    producer(r, legacy, {"entry.txt": b"Old entry", old_dest: b"Hand-copied", "keep.txt": b"Keep"})
    write(r.root, "edit.txt", b"Owned edit")
    for suffix, extras in [("omit", {}), ("empty", {"include_sources": []})]:
        receipt = checkpoint(
            r, pin(suffix), ["edit.txt"], entry="entry.txt", base_version=legacy, **extras
        )
        assert "included_sources" not in receipt["source_closure"]
        assert old_dest in file_rows(receipt["source_closure"])
    first = checkpoint(
        r,
        LANDING,
        ["edit.txt"],
        entry="entry.txt",
        base_version=legacy,
        exclude_paths=[old_dest],
        include_sources=[HERO],
    )
    publish(r, LANDING, first, kind="scene")
    assert file_rows(first["source_closure"])[old_dest]["origin"]["sha256"] == sha(b"Pinned old")
    for label, kwargs, expected, missing in [
        ("retain", {}, [HERO], "linked/new.txt"),
        ("detach", {"include_sources": []}, None, old_dest),
        ("replace", {"include_sources": [next_hero]}, [next_hero], old_dest),
        ("two-revisions", {"include_sources": [HERO, next_hero]}, [HERO, next_hero], "missing.txt"),
    ]:
        body = checkpoint_request(
            r, label, ["edit.txt"], entry="entry.txt", base_version=LANDING, **kwargs
        )
        receipt = r.sources.checkpoint(r.caller, body)
        closure = receipt["source_closure"]
        rows = file_rows(closure)
        assert closure.get("included_sources") == expected
        assert ("included_sources" in closure) == bool(expected)
        assert missing not in rows and "keep.txt" in rows
        assert rows["entry.txt"]["origin"]["commit"] == receipt["source_commit"]
        if label == "retain":
            with pytest.raises(DomainError, match="idempotency conflict"):
                r.sources.checkpoint(r.caller, {**body, "include_sources": []})


@pytest.mark.parametrize("after", [False, True])
def test_composed_acknowledgement_loss_and_idempotency(runtime, monkeypatch, after):
    r = runtime
    producer(r, HERO, {"hero.txt": b"Hero"})
    producer(r, WALK, {"walk.txt": b"Walk"})
    write(r.root, "staged.txt", b"Independent staging")
    git(r.root, "add", "staged.txt")
    staged = git(r.root, "ls-files", "-s", "staged.txt")
    write(r.root, "scene.txt", b"Scene")
    body = checkpoint_request(
        r, "ack-loss", ["scene.txt"], include_sources=[WALK, HERO], dependencies=[WALK, HERO]
    )

    def interrupt(root, *args, **kwargs):
        if args[:2] == ("update-ref", "--stdin"):
            if after:
                git(root, *args, **kwargs)
            raise RuntimeError("Interrupted ref acknowledgement")
        return git(root, *args, **kwargs)

    monkeypatch.setattr(checkpoint_module, "git", interrupt)
    with pytest.raises(RuntimeError, match="acknowledgement"):
        r.sources.checkpoint(r.caller, body)
    original_ref = git(r.root, "rev-parse", "HEAD")
    monkeypatch.setattr(checkpoint_module, "git", git)
    receipt = r.sources.checkpoint(
        r.caller, {**body, "include_sources": [HERO, WALK], "dependencies": [HERO, WALK]}
    )
    assert receipt["replayed"] and receipt["state"] == "completed"
    assert (receipt["source_commit"].encode() == original_ref.strip()) == after
    assert r.sources.checkpoint(r.caller, body) == receipt
    assert git(r.root, "ls-files", "-s", "staged.txt") == staged
    with pytest.raises(DomainError, match="idempotency conflict"):
        r.sources.checkpoint(r.caller, {**body, "include_sources": [HERO]})


def test_collision_diagnostics_do_not_forward_unsafe_paths_or_refs():
    def fail(destination, reference):
        try:
            composition.inclusion_collision(
                destination, reference=reference, consuming_version=LANDING
            )
        except DomainError as exc:
            return json.loads(tool_failure(exc, "publish_outputs").text_result_for_llm)["details"]

    assert fail(HERO_BLEND, HERO) == dict(
        destination=HERO_BLEND, reference=HERO, consuming_version=LANDING
    )
    assert fail("assets/.env.private", {"item_id": "/secret", "version_id": "bad"}) == {
        "consuming_version": LANDING
    }


def test_same_batch_inclusion_is_verified_and_cycle_rejected_atomically(runtime):
    r = runtime
    write(r.root, "hero.txt", b"Hero")
    write(r.root, "scene.txt", b"Scene")
    hero_receipt = checkpoint(r, HERO, ["hero.txt"])
    scene_receipt = checkpoint(r, LANDING, ["scene.txt"])
    scene = scene_receipt["source_closure"]
    scene.update(included_sources=[HERO], dependencies=[HERO])
    scene["files"].extend(hero_receipt["source_closure"]["files"])
    scene["digest"] = closure_digest(scene)
    body = submission(r, LANDING, scene_receipt, inline=True, kind="scene")
    body["items"].extend(submission(r, HERO, hero_receipt, inline=True)["items"])
    cyclic = copy.deepcopy(body)
    cyclic["items"][1]["derived_from"] = [LANDING]
    assert authoring_validation(r, cyclic)["issues"][0]["code"] == "source_conflict"
    with pytest.raises(DomainError, match="Dependency cycle"):
        r.catalogue.publish(r.caller, cyclic)
    with r.store.transaction(write=False) as tx:
        assert tx.all("versions") == []
        assert tx.connection.execute("SELECT COUNT(*) FROM relationship_edges").fetchone()[0] == 0
    assert authoring_validation(r, body)["valid"]
    r.catalogue.publish(r.caller, body)


def test_lfs_declared_bound_fails_before_opening_object(runtime, monkeypatch):
    r = runtime
    lfs(r.root)
    receipt = producer(r, HERO, {HERO_BLEND: b"Hero"})
    object_id = (
        git(
            r.root,
            "hash-object",
            "-w",
            "--stdin",
            data=(
                f"version https://git-lfs.github.com/spec/v1\noid sha256:{sha(b'Hero')}\nsize {composition.MAX_BYTES + 1}\n".encode()
            ),
        )
        .decode()
        .strip()
    )
    git(r.root, "update-index", "--cacheinfo", "100644", object_id, HERO_BLEND)
    git(r.root, "commit", "-m", "Fixture declares too much data")
    oversized_commit = git(r.root, "rev-parse", "HEAD").decode().strip()
    with r.store.transaction() as tx:
        version = tx.require("versions", HERO["version_id"])
        closure = copy.deepcopy(receipt["source_closure"])
        closure["files"][0]["origin"]["commit"] = oversized_commit
        closure["digest"] = closure_digest(closure)
        tx.put(
            "versions",
            HERO["version_id"],
            {**version, "source_commit": oversized_commit, "source": closure},
            expected_revision=tx.revision("versions", HERO["version_id"]),
        )

    def lfs_should_not_open(*args):
        pytest.fail("Declared size must be checked before accessing the local LFS store")

    monkeypatch.setattr(r.sources, "lfs_object", lfs_should_not_open)
    write(r.root, "scene.txt", b"Scene")
    refs = git(r.root, "show-ref")
    with pytest.raises(DomainError, match="512 MiB"):
        checkpoint(r, LANDING, ["scene.txt"], include_sources=[HERO])
    assert git(r.root, "show-ref") == refs
    with r.store.transaction(write=False) as tx:
        assert tx.get("checkpoint_intents", LANDING["version_id"], required=False) is None


@pytest.mark.skipif(
    not os.getenv("PROSCENIUM_MEASURE_COMPOSITION"), reason="Opt-in 512 MiB offline measurement"
)
def test_composition_bound_measurement(runtime):
    r = runtime
    unit = 128 * 1024
    payload = b"m" * unit
    origin = retain(r, "measurement-content", payload)
    refs = [pin(f"source-{i:03}") for i in range(128)]
    destinations = 0
    with r.store.transaction() as tx:
        for i, ref in enumerate(refs):
            files = [
                dict(destination=f"payload/{i:03}/{j:02}.motion", origin=origin)
                for j in range(31 if i == 127 else 32)
            ]
            destinations += len(files)
            closure = dict(
                entry_path=files[0]["destination"],
                files=files,
                dependencies=[],
                required_runtime=dict(application="proscenium", version="1.0", features=[]),
            )
            closure["digest"] = closure_digest(closure)
            tx.put(
                "versions",
                ref["version_id"],
                dict(**ref, project_id=r.caller.project_id, source=closure),
            )
    assert destinations + 1 == composition.MAX_DESTINATIONS
    assert (destinations + 1) * unit == composition.MAX_BYTES
    lfs(r.root)
    write(r.root, LANDING_BLEND, payload)
    tracemalloc.start()
    start = perf_counter()
    receipt = checkpoint(r, LANDING, [LANDING_BLEND], include_sources=refs)
    checkpoint_seconds = perf_counter() - start
    _, checkpoint_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    tracemalloc.start()
    start = perf_counter()
    target = r.sources.resolve(r.caller.project_id, receipt["source_closure"])
    resolve_seconds = perf_counter() - start
    _, resolve_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert len(receipt["source_closure"]["files"]) == composition.MAX_DESTINATIONS
    assert (target / LANDING_BLEND).read_bytes() == payload
    assert (target / "payload/127/30.motion").read_bytes() == payload
    print(
        f"composition limits: checkpoint {checkpoint_seconds:.3f}s peak traced {checkpoint_peak / 2**20:.2f} MiB; materialise {resolve_seconds:.3f}s peak traced {resolve_peak / 2**20:.2f} MiB"
    )
