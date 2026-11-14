"""Synthetic negative assembly cases. Not native/codec/UI acceptance evidence."""

import asyncio

import pytest
from integration.workflows.conftest import environment as environment
from integration.workflows.test_assembly import sequence_fixture
from proscenium.api.errors import DomainError
from proscenium.workflows.assembly import prepared


@pytest.mark.parametrize(
    "failure", ["missing-receipt", "corrupt-image", "unavailable-image", "symlink-image"]
)
def test_missing_or_corrupt_input_never_admits_complete_film(environment, monkeypatch, failure):
    env = environment
    _, receipts, body = sequence_fixture(env, monkeypatch)
    store = env.services.registry.project_store("project-test")
    receipt = receipts[0]
    if failure == "missing-receipt":
        with store.transaction() as tx:
            tx.connection.execute(
                "DELETE FROM records WHERE namespace='frames' AND id=?", (receipt["receipt_id"],)
            )
    else:
        artifacts = env.services.extension("artifacts")
        path = artifacts.ready_path("project-test", receipt["artifact_id"])
        if failure == "corrupt-image":
            path.chmod(0o600)
            path.write_bytes(b"corrupt synthetic frame")
        elif failure == "symlink-image":
            alternate = path.with_name("alternate")
            alternate.write_bytes(path.read_bytes())
            path.unlink()
            path.symlink_to(alternate)
        else:
            with store.transaction() as tx:
                artifact = tx.require("artifacts", receipt["artifact_id"])
                tx.put(
                    "artifacts",
                    artifact["artifact_id"],
                    {**artifact, "state": "unavailable"},
                    expected_revision=tx.revision("artifacts", artifact["artifact_id"]),
                )

    async def run():
        try:
            with pytest.raises(DomainError):
                admission, sequence, mapping = prepared(env.services, env.caller, body)
                env.services.extension("previews").assembly(
                    env.caller, admission, sequence, mapping
                )
            with store.transaction(write=False) as tx:
                assert not [job for job in tx.all("jobs") if job["kind"] == "encode"]
                assert not tx.all("preview_assemblies")
        finally:
            await env.workflows.close()

    asyncio.run(run())
