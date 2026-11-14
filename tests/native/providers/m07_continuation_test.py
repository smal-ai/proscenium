"""Offline continuation safety tests. No real service/GPU/native call occurs here."""

import json
import runpy
from pathlib import Path

import httpx
import pytest

REPO = Path(__file__).parents[3]


def module():
    return runpy.run_path(
        str(REPO / "tools/comfy/prove_generation.py"), run_name="m07_continuation_offline"
    )


def test_window_identity_is_explicit_and_never_grants_arbitrary_continuations(tmp_path):
    code = module()
    function = code["continue_original"]
    function.__globals__["SCRATCH"] = tmp_path
    with pytest.raises(ValueError):
        function("invented-extra-inference-window")
    assert list(tmp_path.iterdir()) == []


def test_window_and_old_receipts_are_write_once(tmp_path):
    code = module()
    path = tmp_path / "retained.json"
    code["write_once"](path, {"original": True})
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        code["write_once"](path, {"original": False})
    assert path.read_bytes() == before


def test_existing_window_report_replays_without_opening_services(tmp_path, monkeypatch):
    code = module()
    function = code["continue_original"]
    function.__globals__["SCRATCH"] = tmp_path
    directory = tmp_path / "continuations/w07-continuation-b"
    directory.mkdir(parents=True)
    report = directory / "report.json"
    report.write_text(
        json.dumps({"after": {"job": {"job_id": "w07-live-initial", "state": "planned"}}})
    )
    before = report.read_bytes()
    monkeypatch.setattr(
        code["Services"],
        "create",
        lambda _: pytest.fail("No service or model effect on receipt replay"),
    )
    function("w07-continuation-b", drop_known_ack=True)
    assert report.read_bytes() == before


def test_known_ack_loss_records_actual_id_and_refuses_a_second_prompt(tmp_path):
    code = module()
    transport = code["ContinuationHTTP"](tmp_path, drop_known_ack=True)
    transport.delegate.close()
    requests = []

    def fake(request):
        requests.append(request)
        return httpx.Response(200, json={"prompt_id": "synthetic-known-id"})

    transport.delegate = httpx.MockTransport(fake)
    with httpx.Client(transport=transport) as client:
        with pytest.raises(httpx.ReadTimeout):
            client.post(
                "http://offline.invalid/prompt",
                json={"client_id": "synthetic-client", "prompt": {}},
            )
        assert (
            json.loads((tmp_path / "known-ack-loss.json").read_text())["prompt_id"]
            == "synthetic-known-id"
        )
        with pytest.raises(FileExistsError):
            client.post(
                "http://offline.invalid/prompt",
                json={"client_id": "synthetic-client", "prompt": {}},
            )
    assert len(requests) == 1
