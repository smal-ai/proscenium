"""Static final-script preflight and private before-snapshot. No native effects."""

import ast
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
OLD = Path.home() / ".cache/proscenium-development/milestones/m05-20261114T165850"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_source(root, path):
    """Pure proposed path contract, not execution of model-authored Blender code."""
    root = root.resolve(strict=True)
    lexical = Path(path)
    if not lexical.is_absolute():
        lexical = root / lexical
    assert lexical.is_relative_to(root)
    assert not any(part.is_symlink() for part in (lexical, *lexical.parents))
    canonical = lexical.resolve(strict=True)
    assert canonical.is_relative_to(root)
    return canonical


def main():
    proof = Path(sys.argv[1]).resolve()
    proof.mkdir(mode=0o700, parents=True, exist_ok=False)
    project = OLD / "project"
    original = project / "tools/history_adopt_completion.py"
    accepted = REPO / "docs/evidence/m05-adoption/model-authored-history_adopt_completion.py.txt"
    assert original.read_bytes() == accepted.read_bytes()
    tree = ast.parse(original.read_text())
    assert any(isinstance(node, ast.FunctionDef) and node.name == "safe" for node in tree.body)
    with tempfile.TemporaryDirectory(dir=proof) as directory:
        root = Path(directory) / "project"
        root.mkdir()
        (root.parent / "outside").write_bytes(b"outside-project rejection fixture")
        (root / "assets").mkdir()
        (root / "scenes/fixture").mkdir(parents=True)
        target = root / "assets/asset.blend"
        target.write_bytes(b"pure path fixture, not Blender output")
        assert canonical_source(root, "scenes/fixture/../../assets/asset.blend") == target
        for candidate, exception in (
            ("missing.blend", FileNotFoundError),
            ("../outside", AssertionError),
        ):
            try:
                canonical_source(root, candidate)
            except exception:
                pass
            else:
                raise AssertionError(f"Guard accepted {candidate}")
        (root / "alias").symlink_to(root / "assets", target_is_directory=True)
        try:
            canonical_source(root, "alias/asset.blend")
        except AssertionError:
            pass
        else:
            raise AssertionError("Lexical symlink rejection was lost")
    review = {
        "source_sha256": sha(original),
        "source_lines": len(original.read_text().splitlines()),
        "canonical_helper_cases_passed": [
            "in-project dot components",
            "missing path rejected",
            "escaping path rejected",
            "lexical symlink rejected",
        ],
        "required_model_edits": [
            "safe(): retain lexical scope/symlink guard, then resolve(strict=True), then canonical containment",
            "Fresh history_adopt_final.py and history-adoption-final.json; current native request/work/context IDs",
            "Only the new live native-final-allocation folder and current report excluded from protected set; old reports protected",
            "Canonical sorted blend_paths and same-directory relative library references for stable pre/post-save dependency equality",
            "Fresh scene.adopting-final.blend sibling; explicit compress=False makes BLENDER header assertion authoritative",
            "Preserve exact-ID selector, scene state equality, protected bytes and all scope/missing-file guards",
        ],
        "remaining_assertions_reviewed": [
            "actual v3 mesh/material/dimensions",
            "library equality and source hash",
            "orphans purge does not change scene state",
            "temporary output freshness/header",
            "dependency equality after same-directory save",
            "protected report set excludes live writer",
            "historical hashes remain immutable",
        ],
        "no_native_or_model_call": True,
    }
    assert not (project / "scenes/fixture/scene.adopting-final.blend").exists()
    (proof / "static-review.json").write_text(json.dumps(review, indent=2))
    connection = sqlite3.connect(f"file:{project}/.proscenium/state.sqlite3?mode=ro", uri=True)
    requests = [
        json.loads(row[0])
        for row in connection.execute("SELECT body FROM records WHERE namespace='requests'")
    ]
    invocations = [
        json.loads(row[0])
        for row in connection.execute("SELECT body FROM records WHERE namespace='invocations'")
    ]
    assert len(requests) == 4 and all(row["receipt"]["state"] == "completed" for row in requests)
    assert all(row["state"] == "completed" for row in invocations)
    before = {
        "requests": requests,
        "head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=project, text=True
        ).strip(),
        "index_sha256": sha(project / ".git/index") if (project / ".git/index").exists() else None,
        "status": subprocess.check_output(
            ["git", "--no-optional-locks", "status", "--porcelain=v1"], cwd=project, text=True
        ),
        "source_files": {
            str(path.relative_to(project)): sha(path)
            for directory in ("assets", "scenes", "research", "reports", "tools")
            for path in (project / directory).rglob("*")
            if path.is_file()
        },
    }
    (proof / "before.json").write_text(json.dumps(before, indent=2))
    snapshot = proof / "private-before"
    snapshot.mkdir(mode=0o700)
    for source, target in (
        (project / ".proscenium/state.sqlite3", snapshot / "project.sqlite3"),
        (OLD / "machine/registry.sqlite3", snapshot / "registry.sqlite3"),
    ):
        with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as original_db:
            with sqlite3.connect(target) as copy:
                original_db.backup(copy)
    for directory in (".git", "assets", "scenes", "research", "reports", "tools"):
        shutil.copytree(project / directory, snapshot / directory.lstrip("."))
    shutil.copytree(
        OLD / "machine/copilot-runtime/home/session-state", snapshot / "native-session-state"
    )
    shutil.copyfile(OLD / "config.json", proof / "original-config.json")
    connection.close()
    print(
        json.dumps({"static_review": "passed", "prior_completed_requests": 4, "proof": str(proof)})
    )


if __name__ == "__main__":
    main()
