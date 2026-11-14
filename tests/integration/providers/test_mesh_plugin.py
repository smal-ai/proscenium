"""Synthetic mesh provider transport with real admission/resources/W02 artefact bridge."""

import copy
import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from integration.image_to_3d.test_preparation import synthetic_glb
from proscenium.api.errors import DomainError
from proscenium.catalogue import install as install_catalogue
from proscenium.providers import register_provider_artifact
from proscenium.providers.comfy import ComfyClient, preflight
from proscenium.providers.hunyuan import FILENAME, graph
from proscenium.providers.plugins import plugin

EVIDENCE = Path(__file__).resolve().parents[3] / "docs/evidence/w09"


@pytest.fixture
def mesh_rig(rig, generation):
    services, caller, mock_artifacts, http, adapter, _ = rig
    services.settings = replace(
        services.settings,
        services=({**services.settings.services[0], "capabilities": ["image_to_3d"]},),
    )
    mock_artifacts.add("workflow-mesh", json.dumps(graph("image-test")).encode())
    contents = [
        (identity, mock_artifacts.get("project-test", identity), path.read_bytes())
        for identity, path in mock_artifacts.paths.items()
    ]
    del services.extensions["artifacts"]
    install_catalogue(services)
    artifacts = services.extension("artifacts")
    for identity, record, data in contents:
        promoted = artifacts.promote(
            "project-test",
            identity,
            data,
            kind=record["kind"],
            purpose=record["purpose"],
            media_type=record["media_type"],
        )
        with services.registry.project_store("project-test").transaction() as tx:
            artifacts.put(tx, promoted)
    adapter.registrar = lambda caller, body: register_provider_artifact(services, caller, body)
    catalog = json.loads((EVIDENCE / "object-info-redacted.json").read_text())
    catalog["ImageOnlyCheckpointLoader"]["input"]["required"]["ckpt_name"][0] = [FILENAME]
    http.catalog = lambda: catalog
    payload = {"bytes": synthetic_glb(), "no_output": False, "error": False}

    def transport(request):
        if request.url.path == "/view":
            http.calls.append(request)
            assert request.url.params["filename"] == "shape.glb"
            return httpx.Response(200, content=payload["bytes"])
        response = http(request)
        if request.url.path.startswith("/history"):
            body = response.json()
            for entry in body.values():
                entry["outputs"] = {
                    "82": {
                        "3d": []
                        if payload["no_output"]
                        else [
                            {"filename": "shape.glb", "subfolder": "mesh", "type": "output"},
                        ]
                    }
                }
                if payload["error"]:
                    entry["status"] = {"completed": True, "status_str": "error"}
            return httpx.Response(200, json=body)
        return response

    with httpx.Client(transport=httpx.MockTransport(transport), trust_env=False) as client:
        adapter.client = client
        request = {
            **generation,
            "capability": "image_to_3d",
            "workflow_artifact_id": "workflow-mesh",
            "input_artifact_ids": ["image-test"],
            "model_ids": [FILENAME],
            "seed": "1",
        }
        yield services, caller, artifacts, http, adapter, request, payload


def rows(services):
    with services.registry.project_store("project-test").transaction(write=False) as tx:
        return [
            tuple(row)
            for row in tx.connection.execute(
                "SELECT id,task_id,target_id,state FROM budget_reservations",
            )
        ]


def test_mesh_uses_existing_admission_and_real_worker_bridge(mesh_rig):
    services, caller, artifacts, http, adapter, request, payload = mesh_rig
    job, replay = adapter.submit(caller, request)
    assert not replay and job["kind"] == "mesh_generation" and not http.calls
    frozen = services.jobs.inputs("project-test", job["job_id"])
    assert frozen["plugin"]["capability"] == "image_to_3d"
    assert frozen["plugin"]["revision"] == "hunyuan3d-native-2.0-1"
    assert len(frozen["plugin"]["expected_model_hashes"][FILENAME]) == 64
    assert frozen["prompt"] == "One red square"  # provenance, absent from model graph
    result = adapter.pump("project-test")[0]
    assert result["state"] == "succeeded", adapter.execution("project-test", job["job_id"])
    assert len(result["artifact_ids"]) == 2
    glb = artifacts.get("project-test", result["artifact_ids"][0])
    assert (glb["kind"], glb["media_type"]) == ("source_blob", "model/gltf-binary")
    assert artifacts.ready_path("project-test", glb["artifact_id"]).read_bytes() == payload["bytes"]
    metadata = json.loads(
        artifacts.ready_path("project-test", result["artifact_ids"][-1]).read_bytes()
    )
    assert metadata["output_inspections"][0]["inspection"]["triangles"] == 1
    assert metadata["inputs"]["references"]["image-test"]["sha256"]
    assert rows(services)[0][3] == "consumed"
    assert adapter.submit(caller, request) == (result, True)
    assert adapter.pump("project-test") == []
    assert sum(r.method == "POST" and r.url.path == "/prompt" for r in http.calls) == 1
    assert {r.url.host for r in http.calls} == {"provider-a.test"}
    with services.resources.store.transaction(write=False) as tx:
        assert tx.connection.execute("SELECT * FROM resource_claims").fetchall() == []


@pytest.mark.parametrize("accepted", [True, False])
def test_mesh_timeout_reconciles_original_without_resubmit(mesh_rig, accepted):
    services, caller, _, http, adapter, request, _ = mesh_rig
    http.timeout_submit, http.accept_before_timeout = True, accepted
    adapter.submit(caller, request)
    assert adapter.pump("project-test")[0]["state"] == "outcome_uncertain"
    original = adapter.execution("project-test", request["job_id"])
    result = adapter.pump("project-test")[0]
    assert result["state"] == ("succeeded" if accepted else "outcome_uncertain")
    assert (
        adapter.execution("project-test", request["job_id"])["client_id"] == original["client_id"]
    )
    assert sum(r.method == "POST" and r.url.path == "/prompt" for r in http.calls) == 1
    assert len(rows(services)) == 1
    assert rows(services)[0][3] == ("consumed" if accepted else "reserved")


def test_mesh_waits_resource_without_upload_or_second_reservation(mesh_rig):
    services, caller, _, http, adapter, request, _ = mesh_rig
    http.queue_busy = True
    adapter.submit(caller, request)
    assert adapter.pump("project-test")[0]["state"] == "waiting_resource"
    assert not [r for r in http.calls if r.method == "POST"]
    http.queue_busy = False
    assert adapter.pump("project-test")[0]["state"] == "succeeded"
    assert len(rows(services)) == 1


@pytest.mark.parametrize("failure", ["error", "no_output", "invalid_glb"])
def test_mesh_failure_does_not_fabricate_asset_or_retry(mesh_rig, failure):
    services, caller, _, http, adapter, request, payload = mesh_rig
    if failure == "invalid_glb":
        payload["bytes"] = b"not a GLB"
    else:
        payload[failure] = True
    adapter.submit(caller, request)
    result = adapter.pump("project-test")[0]
    assert result["state"] == ("failed" if failure == "error" else "outcome_uncertain")
    assert not result["artifact_ids"] and rows(services)[0][3] == "consumed"
    adapter.pump("project-test")
    assert sum(r.method == "POST" and r.url.path == "/prompt" for r in http.calls) == 1


def test_rejected_mesh_releases_original_reservation(mesh_rig):
    services, caller, _, http, adapter, request, _ = mesh_rig
    http.reject_submit = True
    adapter.submit(caller, request)
    assert adapter.pump("project-test")[0]["state"] == "failed"
    assert rows(services)[0][3] == "released"


@pytest.mark.parametrize("fault", ["wrong_model", "no_image", "unused_image", "bypassed_chain"])
def test_hunyuan_rejects_mismatched_conditioning_before_reservation(mesh_rig, fault):
    services, caller, artifacts, http, adapter, request, _ = mesh_rig
    if fault == "wrong_model":
        request["model_ids"] = ["another.safetensors"]
    elif fault == "no_image":
        request["input_artifact_ids"] = []
    elif fault == "unused_image":
        request["input_artifact_ids"].append("image-test")
    else:
        changed = graph("image-test")
        changed["80"]["inputs"]["clip_vision_output"] = ["54", 1]
        artifact = artifacts.promote(
            "project-test",
            "bad-workflow",
            json.dumps(changed).encode(),
            kind="document",
            purpose="metadata",
            media_type="application/json",
        )
        with services.registry.project_store("project-test").transaction() as tx:
            artifacts.put(tx, artifact)
        request["workflow_artifact_id"] = "bad-workflow"
    with pytest.raises(DomainError):
        adapter.submit(caller, request)
    assert not http.calls and not rows(services)


def test_frozen_plugin_revision_cannot_be_substituted(mesh_rig):
    services, caller, _, http, adapter, request, _ = mesh_rig
    job, _ = adapter.submit(caller, request)
    frozen = copy.deepcopy(services.jobs.inputs("project-test", job["job_id"]))
    frozen["plugin"]["revision"] = "unknown-revision"
    with pytest.raises(DomainError):
        adapter._plugin(frozen)
    assert not http.calls
    with pytest.raises(DomainError):
        plugin("image_to_3d", "unknown-revision")


def test_exact_saveglb_union_schema_accepts_mesh_and_rejects_other_socket():
    catalog = json.loads((EVIDENCE / "object-info-redacted.json").read_text())
    catalog["ImageOnlyCheckpointLoader"]["input"]["required"]["ckpt_name"][0] = [FILENAME]
    capability = plugin("image_to_3d")
    bound = capability.bind_graph(
        graph("image"), "not conditioning", "1", {"image": "input.png"}, [FILENAME]
    )
    preflight(bound, catalog, [FILENAME])
    bound["82"]["inputs"]["mesh"] = ["54", 1]
    with pytest.raises(DomainError):
        preflight(bound, catalog, [FILENAME])


def test_streamed_output_byte_cap():
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"abcd"))
    ) as http:
        client = ComfyClient("http://provider.test", http)
        with pytest.raises(DomainError):
            client.download({"filename": "shape.glb"}, maximum_bytes=3)
