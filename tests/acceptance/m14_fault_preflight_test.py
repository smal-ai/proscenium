"""Pure preflight: execute the installed frame-loop AST with fake render I/O, no native spawn."""

import ast
import importlib.util
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from proscenium.contracts import validate
from proscenium.previews import journal

DRIVER = Path(__file__).resolve().parents[1] / "native/previews/m14_fault_driver.py"
module = importlib.util.spec_from_file_location("m14_fault_driver", DRIVER)
assert module is not None and module.loader is not None
driver = importlib.util.module_from_spec(module)
module.loader.exec_module(driver)


def loop():
    tree = ast.parse(Path(journal.__file__).with_name("blender_worker.py").read_text())
    main = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    node = next(
        node
        for node in main.body
        if isinstance(node, ast.For)
        and isinstance(node.target, ast.Name)
        and node.target.id == "frame"
    )
    return compile(
        ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])),
        "<installed-production-frame-loop>",
        "exec",
    )


def context(root, rendered, attempt):
    (root / "frames").mkdir(parents=True, exist_ok=True)
    scene = SimpleNamespace(
        name="pure-fixture",
        camera=SimpleNamespace(matrix_world=[]),
        render=SimpleNamespace(filepath=None),
        frame_set=lambda frame: None,
    )

    def render(**kwargs):
        path = Path(scene.render.filepath)
        rendered.append(int(path.stem.removeprefix(".pending-")))
        path.write_bytes(b"PURE FIXTURE, NOT A NATIVE PNG")

    return dict(
        root=root,
        spec=driver.plan()["spec"],
        scene=scene,
        static_camera=None,
        dependencies=lambda *args: None,
        bpy=SimpleNamespace(ops=SimpleNamespace(render=SimpleNamespace(render=render))),
        source=root,
        closure={},
        read=journal.read,
        sha=journal.sha,
        output=journal.output,
        atomic=journal.atomic,
        time=time,
        task=dict(
            attempt_id=attempt, project_id="p", job_id="j", version_id="v", input_digest="a" * 64
        ),
    )


def test_installed_loop_receipts_frame1_then_rejects_junk_before_frame2_render(tmp_path):
    rendered: list[int] = []
    env = context(tmp_path, rendered, "first")
    junk = tmp_path / "frames/00000002.png"
    junk.write_bytes(driver.INJECTED)
    with pytest.raises(ValueError, match=driver.GUARD):
        exec(loop(), env)
    assert rendered == [1]
    assert journal.read(tmp_path / "frame-00000001.json")["source_frame"] == 1
    assert not (tmp_path / "frame-00000002.json").exists()
    assert junk.read_bytes() == driver.INJECTED


def test_installed_loop_skips_adopted_verified_frame1_and_renders_only_2_3(tmp_path):
    rendered: list[int] = []
    env = context(tmp_path, rendered, "second")
    data = b"PURE FIXTURE ADOPTED REGISTERED BYTES"
    (tmp_path / "frames/00000001.png").write_bytes(data)
    journal.atomic(
        tmp_path / "frame-00000001.json",
        {"output_path": "frames/00000001.png", "sha256": journal.sha(data)},
    )
    exec(loop(), env)
    assert rendered == [2, 3]
    assert not (tmp_path / "timing-00000001.json").exists()


def test_hash_mismatch_cannot_be_reused(tmp_path):
    rendered: list[int] = []
    env = context(tmp_path, rendered, "second")
    (tmp_path / "frames/00000001.png").write_bytes(b"changed")
    journal.atomic(
        tmp_path / "frame-00000001.json", {"output_path": "frames/00000001.png", "sha256": "a" * 64}
    )
    with pytest.raises(ValueError, match="Previously receipted frame changed"):
        exec(loop(), env)
    assert rendered == []


def test_plan_schema_counts_source_import_and_seals():
    plan = driver.plan()
    validate("RenderSpec", plan["spec"])
    assert plan["workers"] == 2 and plan["actual_frames_planned"] == 3 < 4
    assert plan["encodes_planned"] == 1
    assert plan["version"]["source_commit"] == driver.COMMIT
    assert plan["version"]["source"]["files"][0]["origin"]["sha256"] == driver.BLEND_SHA
    spec = plan["spec"]
    assert spec["frames"] == {"start": 1, "end": 3}
    assert spec["samples"] == 8 and spec["dimensions"] == {"width": 640, "height": 360}
    assert set(plan["renderer_hashes"]) == set(journal.RENDERER_FILES)
    # A three-frame receipt group cannot encode after only frame 1. One group on retry.
    groups = [range(first, min(first + 3, 3) + 1) for first in range(1, 4, 4)]
    assert sum(all(frame in {1} for frame in group) for group in groups) == 0
    assert sum(all(frame in {1, 2, 3} for frame in group) for group in groups) == 1
    validate(
        "SelectionSave",
        dict(
            schema_version="1.0",
            operation_id="preflight-selection",
            project_id="p",
            selection_id="selection",
            expected_revision=0,
            slot={"kind": "item", "item_id": plan["version"]["item_id"]},
            choice={
                "kind": "version",
                "version": {key: plan["version"][key] for key in ("item_id", "version_id")},
            },
            action="select",
        ),
    )


def test_record_paths_and_helper_imports_are_production_owned(tmp_path):
    root = tmp_path / "attempt"
    root.mkdir()
    assert journal.output(root, "frames/00000002.png") == root / "frames/00000002.png"
    with pytest.raises(ValueError):
        journal.output(root, "../elsewhere.png")
    tree = ast.parse(Path(journal.__file__).with_name("blender_worker.py").read_text())
    directory = Path(journal.__file__).parent
    imported = {
        node.module + ".py"
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module
        and (directory / (node.module + ".py")).exists()
    }
    assert imported | {"blender_worker.py"} == set(journal.RENDERER_FILES)
    # Retry adoption must precede launch claim and spawn in the unchanged production launcher.
    launcher = (
        directory.joinpath("runner.py")
        .read_text()
        .split("def launch(service", 1)[1]
        .split("def cancel(", 1)[0]
    )
    assert (
        launcher.index("reusable =")
        < launcher.index("adopted =")
        < launcher.index("O_EXCL")
        < launcher.index("subprocess.Popen")
    )


def test_operator_reporting_keeps_revisions_without_overwriting_worker_journals(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(driver, "ROOT", tmp_path)
    journal.atomic(tmp_path / "result.json", {"historical": "initial failed driver report"})
    original = (tmp_path / "result.json").read_bytes()
    driver.report({"stage": 1})
    driver.report({"stage": 2})
    assert journal.read(tmp_path / "operator-reports/0001.json") == {"stage": 1}
    assert journal.read(tmp_path / "operator-reports/0002.json") == {"stage": 2}
    assert journal.read(tmp_path / "report-latest.json") == {"stage": 2}
    assert (tmp_path / "result.json").read_bytes() == original
