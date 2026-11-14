"""Synthetic offline checks only. These do not establish E04 or M10."""

import copy
import io
import json
import struct
from pathlib import Path

import pytest

from tools.image_to_3d import weights
from tools.image_to_3d.candidate import FILENAME, graph
from tools.image_to_3d.glb import inspect_glb
from tools.image_to_3d.workflow import bind, output_files, preflight

EVIDENCE = Path(__file__).resolve().parents[3] / "docs/evidence/w09"


def bound_graph():
    return bind(graph("reference"), "reference", "conditioning-00.png", "1")


def catalog_with_model():
    catalog = json.loads((EVIDENCE / "object-info-redacted.json").read_text())
    # Explicitly synthetic fixture. The real independent service has no checkpoint.
    catalog["ImageOnlyCheckpointLoader"]["input"]["required"]["ckpt_name"][0] = [FILENAME]
    return catalog


def test_matches_official_graph_topology_and_parameters():
    official = json.loads((EVIDENCE / "official-template.json").read_text())
    subgraph = official["definitions"]["subgraphs"][0]
    expected = graph("reference")
    for node in subgraph["nodes"]:
        assert expected[str(node["id"])]["class_type"] == node["type"]
    for link in subgraph["links"]:
        if link["origin_id"] < 0 or link["target_id"] < 0:
            continue
        target = next(n for n in subgraph["nodes"] if n["id"] == link["target_id"])
        name = target["inputs"][link["target_slot"]]["name"]
        assert expected[str(link["target_id"])]["inputs"][name] == [
            str(link["origin_id"]),
            link["origin_slot"],
        ]
    official_kinds = {n["type"]: n for n in subgraph["nodes"]}
    assert official_kinds["KSampler"]["widgets_values"][2:] == [20, 8, "euler", "normal", 1]
    assert official_kinds["VAEDecodeHunyuan3D"]["widgets_values"] == [8000, 256]
    assert official_kinds["EmptyLatentHunyuan3Dv2"]["widgets_values"] == [3072, 1]
    assert official_kinds["ModelSamplingAuraFlow"]["widgets_values"] == [1, "flow"]


def test_preflight_actual_catalog_fails_missing_weights():
    catalog = json.loads((EVIDENCE / "object-info-redacted.json").read_text())
    with pytest.raises(ValueError, match="unavailable choice"):
        preflight(bound_graph(), catalog)


def test_preflight_supports_actual_multitype_save_socket():
    preflight(bound_graph(), catalog_with_model())


@pytest.mark.parametrize("mutation", ["node", "link", "bound", "choice", "model", "extra"])
def test_preflight_rejects_bad_schema_inputs(mutation):
    candidate, catalog = bound_graph(), catalog_with_model()
    if mutation == "node":
        del catalog["SaveGLB"]
    elif mutation == "link":
        candidate["82"]["inputs"]["mesh"] = ["51", 0]
    elif mutation == "bound":
        candidate["3"]["inputs"]["steps"] = 0
    elif mutation == "choice":
        candidate["81"]["inputs"]["algorithm"] = "invented"
    elif mutation == "model":
        candidate["54"]["inputs"]["ckpt_name"] = "wrong.safetensors"
    else:
        candidate["3"]["inputs"]["unexpected"] = True
    with pytest.raises(ValueError):
        preflight(candidate, catalog)


@pytest.mark.parametrize("seed", ["-1", "1.1", "9007199254740992", "", "１"])
def test_seed_rejected(seed):
    with pytest.raises(ValueError):
        bind(graph("reference"), "reference", "conditioning.png", seed)


def test_binding_is_exact_and_preserves_input():
    template = graph("reference")
    before = copy.deepcopy(template)
    bound = bind(template, "reference", "conditioning.png", "9007199254740991")
    assert template == before
    assert bound["3"]["inputs"]["seed"] == 2**53 - 1
    template["51"]["inputs"]["image"] = ["54", 0]
    with pytest.raises(ValueError, match="trusted"):
        bind(template, "reference", "conditioning.png", "1")


@pytest.mark.parametrize("filename", ["../escape.png", "/escape.png", "a\\escape.png", "a/./b.png"])
def test_upload_path_rejected(filename):
    with pytest.raises(ValueError):
        bind(graph("reference"), "reference", filename, "1")


def history(filename="result.glb", folder="mesh"):
    return {
        "status": {"status_str": "success", "completed": True},
        "outputs": {"82": {"3d": [{"filename": filename, "subfolder": folder, "type": "output"}]}},
    }


def test_native_saveglb_history_shape():
    assert output_files(history())[0]["filename"] == "result.glb"
    bad = history()
    bad["outputs"]["82"] = {"images": [{"filename": "preview.png"}]}
    with pytest.raises(ValueError):
        output_files(bad)
    bad = history()
    bad["status"]["completed"] = False
    with pytest.raises(ValueError):
        output_files(bad)


@pytest.mark.parametrize(
    "name,folder",
    [
        ("../mesh.glb", "mesh"),
        ("mesh.png", "mesh"),
        ("mesh.glb", "../escape"),
        ("mesh.glb", "/absolute"),
    ],
)
def test_unsafe_history_rejected(name, folder):
    with pytest.raises(ValueError):
        output_files(history(name, folder))


def synthetic_glb(positions=None, indices=(0, 1, 2)):
    positions = positions or ((0, 0, 0), (1, 0, 0), (0, 1, 0))
    binary = b"".join(struct.pack("<3f", *p) for p in positions)
    binary += struct.pack("<3H", *indices) + b"\x00\x00"
    document = {
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": 36},
            {"buffer": 0, "byteOffset": 36, "byteLength": 6},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"},
            {"bufferView": 1, "componentType": 5123, "count": 3, "type": "SCALAR"},
        ],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
    }
    encoded = json.dumps(document).encode()
    encoded += b" " * (-len(encoded) % 4)
    chunks = struct.pack("<II", len(encoded), 0x4E4F534A) + encoded
    chunks += struct.pack("<II", len(binary), 0x004E4942) + binary
    return struct.pack("<4sII", b"glTF", 2, len(chunks) + 12) + chunks


def test_synthetic_glb_is_not_blender_proof(tmp_path):
    path = tmp_path / "synthetic.glb"
    path.write_bytes(synthetic_glb())
    result = inspect_glb(path)
    assert result["vertices"] == 3 and result["triangles"] == 1
    assert result["blender_import_proven"] is False
    assert result["local_bounds"] == [[0, 0, 0], [1, 1, 0]]


@pytest.mark.parametrize(
    "data",
    [
        b"not a glb",
        synthetic_glb(indices=(0, 1, 8)),
        synthetic_glb(positions=((0, 0, 0), (float("nan"), 0, 0), (0, 1, 0))),
        synthetic_glb()[:-1],
    ],
)
def test_bad_glb_rejected(tmp_path, data):
    path = tmp_path / "bad.glb"
    path.write_bytes(data)
    with pytest.raises(ValueError):
        inspect_glb(path)


def test_download_resume_and_no_duplicate_bytes(tmp_path, monkeypatch):
    payload = b"fake checkpoint, never a model"
    destination = tmp_path / "models/checkpoints/test.safetensors"
    monkeypatch.setattr(weights, "WEIGHT_PATH", destination)
    monkeypatch.setattr(weights, "SIZE", len(payload))
    monkeypatch.setattr(weights, "DISK_RESERVE", 0)
    monkeypatch.setattr(weights, "inspect_weights", lambda path: {"path": str(path)})
    calls = []

    class Response(io.BytesIO):
        status = 206
        headers = {"Content-Range": f"bytes 0-{len(payload) - 1}/{len(payload)}"}

    def request(req, timeout):
        calls.append(req)
        return Response(payload)

    monkeypatch.setattr(weights.urllib.request, "urlopen", request)
    assert weights.download()["requested_bytes"] == len(payload)
    assert destination.read_bytes() == payload
    weights.download()
    assert len(calls) == 1


def test_server_ignoring_range_retains_charge_and_partial(tmp_path, monkeypatch):
    monkeypatch.setattr(weights, "WEIGHT_PATH", tmp_path / "models/checkpoints/test.safetensors")
    monkeypatch.setattr(weights, "SIZE", 100)
    monkeypatch.setattr(weights, "DISK_RESERVE", 0)

    class Response(io.BytesIO):
        status = 200
        headers = {}

    monkeypatch.setattr(weights.urllib.request, "urlopen", lambda *a, **k: Response(b"bad"))
    with pytest.raises(ValueError, match="Range"):
        weights.download()
    state = json.loads(next(tmp_path.rglob("*.download.json")).read_text())
    assert state["requested_bytes"] == 100
    assert next(tmp_path.rglob("*.part")).stat().st_size == 0


def test_partial_download_resumes_exact_offset(tmp_path, monkeypatch):
    payload = b"synthetic checkpoint bytes"
    destination = tmp_path / "models/checkpoints/test.safetensors"
    destination.parent.mkdir(parents=True)
    partial = destination.with_suffix(".part")
    partial.write_bytes(payload[:5])
    destination.with_suffix(".download.json").write_text(
        json.dumps(
            {
                "url": weights.URL,
                "sha256": weights.SHA256,
                "requested_bytes": 5,
            }
        )
    )
    monkeypatch.setattr(weights, "WEIGHT_PATH", destination)
    monkeypatch.setattr(weights, "SIZE", len(payload))
    monkeypatch.setattr(weights, "DISK_RESERVE", 0)
    monkeypatch.setattr(weights, "inspect_weights", lambda path: {"path": str(path)})

    class Response(io.BytesIO):
        status = 206
        headers = {"Content-Range": f"bytes 5-{len(payload) - 1}/{len(payload)}"}

    def request(req, timeout):
        assert req.headers["Range"] == f"bytes=5-{len(payload) - 1}"
        return Response(payload[5:])

    monkeypatch.setattr(weights.urllib.request, "urlopen", request)
    assert weights.download()["requested_bytes"] == len(payload)
    assert destination.read_bytes() == payload
    assert not partial.exists()


def test_download_cap_refuses_before_http(tmp_path, monkeypatch):
    destination = tmp_path / "models/checkpoints/test.safetensors"
    monkeypatch.setattr(weights, "WEIGHT_PATH", destination)
    monkeypatch.setattr(weights, "SIZE", 100)
    monkeypatch.setattr(weights, "DOWNLOAD_CAP", 99)
    monkeypatch.setattr(weights, "DISK_RESERVE", 0)

    def forbidden(*args, **kwargs):
        pytest.fail("must not contact server beyond allocation")

    monkeypatch.setattr(weights.urllib.request, "urlopen", forbidden)
    with pytest.raises(ValueError, match="allocation exhausted"):
        weights.download()


def test_disk_reserve_refuses_before_http(tmp_path, monkeypatch):
    destination = tmp_path / "models/checkpoints/test.safetensors"
    monkeypatch.setattr(weights, "WEIGHT_PATH", destination)
    monkeypatch.setattr(weights, "DISK_RESERVE", 2**64)
    with pytest.raises(ValueError, match="reserve"):
        weights.download()


def test_source_receipts_match_retained_official_bytes():
    import hashlib

    manifest = json.loads((EVIDENCE / "preparation.json").read_text())
    for source in manifest["sources"]:
        data = (EVIDENCE / source["path"]).read_bytes()
        assert len(data) == source["bytes"]
        assert hashlib.sha256(data).hexdigest() == source["sha256"]


def test_prepared_graph_identity_matches_generator():
    import hashlib

    data = (EVIDENCE / "prepared-api-graph.json").read_bytes()
    identity = json.loads((EVIDENCE / "graph-identity.json").read_text())
    assert json.loads(data) == graph("e04-conditioning")
    assert hashlib.sha256(data).hexdigest() == identity["sha256"]
    assert len(data) == identity["bytes"]


def test_existing_generation_contract_and_honest_unavailable(services, caller):
    from dataclasses import replace

    from proscenium.api.errors import DomainError
    from proscenium.contracts import validate
    from proscenium.providers import install

    request = json.loads((EVIDENCE / "generation-request.example.json").read_text())
    validate("GenerationSubmit", request)
    adapter = install(services)
    authorised = replace(caller, operations=("submitGenerationJob",))
    with pytest.raises(DomainError) as error:
        adapter.submit(authorised, request)
    assert error.value.code == "capability_unavailable"
    with services.registry.project_store("project-test").transaction() as tx:
        assert tx.get("jobs", request["job_id"], required=False) is None
        assert tx.get("providers.tasks", request["generation_task_id"], required=False) is None
