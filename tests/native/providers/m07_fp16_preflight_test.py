"""Offline fixture/import preflight, not GPU or SDK acceptance."""

import runpy
import signal
from pathlib import Path
from types import SimpleNamespace

from proscenium.api.composition import configure_services
from proscenium.configuration import Settings
from proscenium.services import Services


def test_owned_fixture_signal_context_does_not_reraise_and_restores_handlers(monkeypatch):
    here = Path(__file__).parent
    monkeypatch.syspath_prepend(str(here))
    code = runpy.run_path(str(here / "m07_fp16_host.py"), run_name="signal_fixture_preflight")
    saved = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    fixture = SimpleNamespace(handle_exit=lambda *_: None)
    with code["FixtureServer"].capture_signals(fixture):
        assert signal.getsignal(signal.SIGTERM) == fixture.handle_exit
    assert {sig: signal.getsignal(sig) for sig in saved} == saved


def test_real_fixture_publication_graphs_and_policy_before_native_effects(tmp_path, monkeypatch):
    here = Path(__file__).parent
    monkeypatch.syspath_prepend(str(here))
    code = runpy.run_path(str(here / "m07_fp16_host.py"), run_name="m07_offline_preflight")
    setup = code["fixtures"]
    root = tmp_path / "run"
    root.mkdir()
    setup.__globals__["ROOT"] = root
    services = Services.create(
        Settings(
            data_directory=root / "machine",
            profiles=(
                dict(
                    profile_id="authoring",
                    name="Offline",
                    lead_model_id="gpt-6.1-sol",
                    worker_model_ids=["gpt-6.1-sol"],
                    scope_status="project_scoped",
                ),
            ),
        )
    )
    try:
        configure_services(services)
        fixture = setup(services)
        project_id = fixture["project"]["project_id"]
        plan = services.extension("catalogue").shot_plan(project_id, "m07-fp16-plan-v1")
        assert len(plan["shots"][0]["frames"]) == 2
        assert all(a["dimensions"] == {"width": 512, "height": 512} for a in fixture["references"])
        with services.registry.project_store(project_id).transaction(write=False) as tx:
            assert tx.all("jobs") == [] and tx.all("requests") == []
        assert (
            "USE_AUTHORITATIVE_HOST_CONTEXT_GENERATION_TASK_ID"
            in (root / "initial-prompt.txt").read_text()
        )
    finally:
        services.close()
