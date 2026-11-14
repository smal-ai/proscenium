"""Offline safety checks. The actual SSH/browser allocation is an explicit CLI run."""

import asyncio
import io
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from tools.operations import ssh_proof
from tools.operations.safety import RecoveryError
from tools.operations.ssh_service import witness_lifespan


def test_sshd_configuration_is_loopback_public_key_and_fixed_forward_only(tmp_path):
    config = ssh_proof.sshd_configuration(tmp_path, 55222, 55223, "fixture")
    for required in (
        "ListenAddress 127.0.0.1",
        "PasswordAuthentication no",
        "UsePAM no",
        "KbdInteractiveAuthentication no",
        "AuthenticationMethods publickey",
        "AllowTcpForwarding local",
        "PermitOpen 127.0.0.1:55223",
        "PermitUserRC no",
        "AllowAgentForwarding no",
        "ForceCommand /usr/bin/false",
    ):
        assert required in config
    assert "/etc/" not in config and ".ssh" not in config


def test_command_receipt_redacts_fixture_paths_not_secret_material(tmp_path):
    result = ssh_proof.command_record(["ssh", "-i", str(tmp_path / "client_key")], tmp_path)
    assert result["argv_redacted"] == ["ssh", "-i", "<PRIVATE_ROOT>/client_key"]
    assert str(tmp_path) not in json.dumps(result)
    assert len(result["redacted_sha256"]) == 64


def test_changed_owned_start_identity_cannot_be_signalled(monkeypatch):
    owned = object.__new__(ssh_proof.Owned)
    owned.start = "original"
    owned.output = io.BytesIO()
    owned.process = type("Process", (), {"pid": 999989, "poll": lambda self: None})()
    monkeypatch.setattr(ssh_proof, "identity", lambda _pid: "different")
    monkeypatch.setattr(ssh_proof.os, "killpg", lambda *_args: pytest.fail("No unrelated signals"))
    with pytest.raises(RecoveryError, match="identity_changed"):
        owned.close()


def test_guard_receipt_is_persisted_before_post_lifespan_signal_reraise():
    order = []

    @asynccontextmanager
    async def original(_app):
        order.append("started")
        try:
            yield
        finally:
            order.append("closed")

    app = SimpleNamespace(router=SimpleNamespace(lifespan_context=original))
    witness_lifespan(app, lambda: order.append("receipt"))

    async def run():
        async with app.router.lifespan_context(app):
            order.append("traffic")
        # Uvicorn's later signal re-raise cannot prevent the already-written receipt.
        raise RuntimeError("Synthetic post-lifespan termination")

    with pytest.raises(RuntimeError, match="post-lifespan"):
        asyncio.run(run())
    assert order == ["started", "traffic", "closed", "receipt"]
