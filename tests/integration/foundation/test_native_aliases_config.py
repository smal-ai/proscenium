import asyncio
import hashlib
import json
import subprocess
from pathlib import Path

from copilot.tools import ToolInvocation
from proscenium.api.composition import configure_services
from proscenium.configuration import Settings
from proscenium.copilot.native import BINARY_SHA256, Configuration
from proscenium.copilot.state import key
from proscenium.copilot.tools import Tools


def test_native_tools_use_explicit_provider_aliases_and_never_return_findings(
    services, monkeypatch
):
    configure_services(services)
    runtime = services.extension("copilot")
    provider = services.extension("providers")
    tools = Tools(services, runtime.state, "project-test")
    services.gate.resume(
        "project-test", reconciliation_evidence="Synthetic admitted native invocation"
    )
    invocation_id = key("synthetic-native", "tool", "synthetic-call")
    with services.registry.project_store("project-test").transaction() as tx:
        tx.put(
            "invocations",
            invocation_id,
            dict(
                invocation_id=invocation_id,
                session_id="synthetic-native",
                agent_id=None,
                state="running",
                owner_epoch=services.ownership.epoch,
                work_id="work-test",
            ),
        )
    ticket = json.loads(Path("docs/contracts/examples/review-ticket.json").read_text())
    review = dict(
        schema_version="1.0",
        review_id=ticket["review_id"],
        project_id="project-test",
        targets=[json.loads(Path("docs/contracts/examples/composite-target.json").read_text())],
        criteria="Synthetic criterion",
        telemetry_artifact_ids=[],
    )
    called = []

    def request(caller, body):
        called.append((caller, body))
        return ticket, False

    monkeypatch.setattr(provider, "request_review", request)
    invocation = ToolInvocation(
        session_id="synthetic-native",
        tool_call_id="synthetic-call",
        tool_name="request_vlm_review",
        arguments=review,
    )
    result = asyncio.run(tools.invoke("request_vlm_review", invocation))
    assert json.loads(result.text_result_for_llm) == ticket
    assert called[0][0].work_id == "work-test" and called[0][1] == review
    monkeypatch.setattr(
        provider, "request_review", lambda c, b: ({**ticket, "findings": ["private advice"]}, False)
    )
    result = asyncio.run(tools.invoke("request_vlm_review", invocation))
    assert json.loads(result.text_result_for_llm) == {"error": "invalid_request"}
    assert "private advice" not in result.text_result_for_llm
    generation = json.loads(Path("docs/contracts/examples/generation.json").read_text())
    generation["project_id"] = "project-test"
    job = json.loads(Path("docs/contracts/examples/owner-review-job.json").read_text())
    monkeypatch.setattr(provider, "submit", lambda c, b: (job, False))
    invocation = ToolInvocation(
        session_id="synthetic-native",
        tool_call_id="synthetic-call",
        tool_name="submit_generation",
        arguments=generation,
    )
    result = asyncio.run(tools.invoke("submit_generation", invocation))
    assert json.loads(result.text_result_for_llm) == job


def test_operator_example_resolves_private_resource_paths_without_execution():
    settings = Settings.load(Path("config.example.json"))
    config = Configuration.load(settings.runtime)
    assert config.worker_model == "gpt-6.1-sol"
    assert config.cli_path.is_absolute() and config.cli_path.name == "copilot"
    with config.cli_path.open("rb") as binary:
        assert hashlib.file_digest(binary, "sha256").hexdigest() == BINARY_SHA256
    assert settings.execution_profiles == {"blender-cpu", "unity-local"}
    assert settings.origin == "http://127.0.0.1:8765"
    assert all(service["available"] is None for service in settings.services)
    assert settings.rendering["claim_path"].startswith(str(Path.home()))
    assert "runtime" not in settings.view()


def test_explicit_missing_cli_configuration_does_not_start_with_defaults(tmp_path):
    result = subprocess.run(
        ["uv", "run", "python", "-m", "proscenium", "--config", str(tmp_path / "missing.json")],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2
    assert "existing private JSON file" in result.stderr
