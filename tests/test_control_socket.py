import asyncio
import json
import os
import socket
import sqlite3
import stat
import sys
import threading
import time
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from proscenium import control
from proscenium.__main__ import main
from proscenium.app import create_app
from proscenium.auth import COOKIE_NAME
from proscenium.configuration import Settings
from proscenium.control import (
    ControlProtocolError,
    ControlServer,
    ControlUnavailableError,
    control_request,
    control_socket_path,
    validate_control_directory,
)
from proscenium.db.store import Store
from proscenium.services import Services


@pytest.fixture
def services(tmp_path):
    services = Services.create(Settings(data_directory=tmp_path / "data"))
    try:
        yield services
    finally:
        services.close()


def _connect(path: Path) -> socket.socket:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        sock.settimeout(2)
        sock.connect(f"/proc/self/fd/{fd}/{path.name}")
        return sock
    except BaseException:
        sock.close()
        raise
    finally:
        os.close(fd)


def _bind(sock: socket.socket, path: Path) -> None:
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        sock.bind(f"/proc/self/fd/{fd}/{path.name}")
    finally:
        os.close(fd)


def _raw_request(path: Path, request: bytes) -> dict:
    with _connect(path) as sock:
        sock.sendall(request)
        sock.shutdown(socket.SHUT_WR)
        with sock.makefile("rb") as stream:
            response = json.loads(stream.readline(16385))
            assert stream.read(1) == b""
            return response


def test_control_pair_bootstraps_real_http_owner_session(tmp_path, caplog):
    settings = Settings(data_directory=tmp_path / "data")
    path = control_socket_path(settings)
    with TestClient(create_app(settings), base_url=settings.origin) as browser:
        info = path.lstat()
        assert stat.S_ISSOCK(info.st_mode)
        assert stat.S_IMODE(info.st_mode) == 0o600

        result = control_request(path, {"action": "pair"})
        assert set(result) == {"ok", "pairing_url", "expires_in_seconds"}
        assert result["ok"] is True
        assert result["expires_in_seconds"] == 900
        url = urlsplit(result["pairing_url"])
        assert url._replace(fragment="").geturl() == settings.origin + "/"
        code = parse_qs(url.fragment)["pair"][0]
        operation = str(uuid4())
        body = {
            "schema_version": "1.0",
            "operation_id": operation,
            "client_nonce": str(uuid4()),
            "pairing_code": code,
        }
        headers = {"Origin": settings.origin, "Idempotency-Key": operation}
        response = browser.post("/api/v1/owner/session", json=body, headers=headers)
        assert response.status_code == 201
        cookie = response.cookies[COOKIE_NAME]
        assert cookie and browser.cookies[COOKIE_NAME] == cookie
        cookie_header = response.headers["set-cookie"].lower()
        assert "httponly" in cookie_header and "samesite=strict" in cookie_header
        csrf = response.json()["csrf_token"]
        assert csrf and csrf != cookie
        assert browser.get("/api/v1/configuration").status_code == 200

        mutation = "/api/v1/configuration/profiles/missing"
        assert browser.put(mutation, json={}, headers=headers).status_code == 403
        authenticated = browser.put(mutation, json={}, headers={**headers, "X-CSRF-Token": csrf})
        assert authenticated.status_code == 400  # Auth passed. The command body is invalid.
        replay = browser.post("/api/v1/owner/session", json=body, headers=headers)
        assert replay.status_code == 200
        assert replay.json() == response.json()
        assert code not in caplog.text

    assert not os.path.lexists(path)


def test_status_reports_only_live_fields_without_writing(tmp_path, monkeypatch):
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    settings = Settings(data_directory=tmp_path / "data", frontend_directory=frontend)
    path = control_socket_path(settings)
    app = create_app(settings, manage_features=False, enable_control_socket=True)
    with TestClient(app, base_url=settings.origin):
        services = app.state.services
        expected = {
            "ok": True,
            "pid": os.getpid(),
            "origin": settings.origin,
            "data_directory": str(settings.data_directory),
            "active_project_id": None,
            "owner_epoch": services.ownership.epoch,
            "frontend_ready": False,
        }
        assert control_request(path, {"action": "status"}) == expected
        (frontend / "index.html").write_text("<!doctype html>")
        with services.registry.store.transaction() as tx:
            tx.put("registry", "active", {"project_id": "project-control"})
        transaction = services.registry.store.transaction

        def read_only(*, write=True):
            assert write is False, "status must not open a write transaction"
            return transaction(write=False)

        monkeypatch.setattr(services.registry.store, "transaction", read_only)
        assert control_request(path, {"action": "status"}) == {
            **expected,
            "active_project_id": "project-control",
            "frontend_ready": True,
        }


def test_unmanaged_injected_services_disable_control_by_default(services):
    with TestClient(create_app(services=services, manage_features=False)):
        assert not os.path.lexists(control_socket_path(services.settings))


@pytest.mark.parametrize("launch", ["app", "app_without_control", "module"])
@pytest.mark.parametrize(
    "directory_type", ["symlink", "unsafe", "dangling", "missing_ancestor", "unsafe_ancestor"]
)
def test_startup_refuses_unsafe_data_directory_before_services_create(
    tmp_path, monkeypatch, launch, directory_type
):
    target = tmp_path / "target"
    target.mkdir()
    if directory_type in {"unsafe", "unsafe_ancestor"}:
        data_directory = target if directory_type == "unsafe" else target / "missing" / "data"
        target.chmod(0o770)
    else:
        alias = tmp_path / "alias"
        alias.symlink_to(tmp_path / "absent" if directory_type == "dangling" else target)
        data_directory = (
            alias / "missing" / "data" if directory_type == "missing_ancestor" else alias
        )

    create = Mock(wraps=Services.create)
    monkeypatch.setattr(Services, "create", create)
    with pytest.raises(PermissionError, match="control directory"):
        if launch != "module":
            enabled = None if launch == "app" else False
            with TestClient(
                create_app(Settings(data_directory=data_directory), enable_control_socket=enabled)
            ):
                pytest.fail("startup unexpectedly succeeded")
        else:
            config = tmp_path / "config.json"
            config.write_text(json.dumps({"data_directory": str(data_directory)}))
            monkeypatch.setattr(sys, "argv", ["proscenium", "--config", str(config)])
            main()
    create.assert_not_called()
    assert list(target.iterdir()) == []
    assert not (tmp_path / "absent").exists()


@pytest.mark.parametrize("mode", [0o775, 0o777])
@pytest.mark.parametrize("enable_control_socket", [True, False])
def test_existing_private_leaf_under_writable_ancestor_is_rejected(
    tmp_path, monkeypatch, mode, enable_control_socket
):
    ancestor = tmp_path / "unsafe"
    ancestor.mkdir()
    ancestor.chmod(mode)
    middle = ancestor / "private"
    middle.mkdir(mode=0o700)
    leaf = middle / "data"
    leaf.mkdir(mode=0o700)
    settings = Settings(data_directory=leaf)
    create = Mock(wraps=Services.create)
    monkeypatch.setattr(Services, "create", create)
    with pytest.raises(PermissionError, match="unexpected owner or permissions"):
        with TestClient(create_app(settings, enable_control_socket=enable_control_socket)):
            pytest.fail("startup accepted an unsafe ancestor")
    create.assert_not_called()
    assert list(leaf.iterdir()) == []
    with pytest.raises(ControlProtocolError, match="unexpected owner or permissions"):
        control_request(control_socket_path(settings), {"action": "status"})


def test_directory_validation_accepts_new_path_under_sticky_temp_root(tmp_path):
    sticky = tmp_path / "sticky"
    sticky.mkdir()
    sticky.chmod(0o1777)
    new = sticky / "new" / "data"
    with pytest.raises(FileNotFoundError):
        validate_control_directory(new)
    assert not new.parent.exists()
    validate_control_directory(new, create=True)
    assert stat.S_IMODE(new.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(new.stat().st_mode) == 0o700
    with pytest.raises(PermissionError, match="unexpected owner or permissions"):
        validate_control_directory(sticky)  # The data directory itself must be private.


def test_foreign_peer_is_closed_without_reading_or_replying(services, monkeypatch):
    issue_pairing = Mock(wraps=services.auth.issue_pairing)
    monkeypatch.setattr(services.auth, "issue_pairing", issue_pairing)
    foreign_uid = Mock(return_value=os.getuid() + 1)

    async def scenario():
        server = ControlServer(services, peer_uid=foreign_uid)
        await server.start()
        try:
            # An idle foreign peer must be disconnected without a request or a timeout.
            with _connect(server.path) as sock:
                assert await asyncio.to_thread(sock.recv, 1) == b""
            with _connect(server.path) as sock:
                sock.sendall(b'{"action":"pair"}\n')
                try:
                    assert await asyncio.to_thread(sock.recv, 1) == b""
                except ConnectionResetError:
                    pass  # The connection was closed with unread data.
        finally:
            await server.close()

    asyncio.run(scenario())
    assert foreign_uid.call_count == 2
    issue_pairing.assert_not_called()


@pytest.mark.parametrize("entry", ["symlink", "file"])
def test_non_socket_is_refused_and_preserved(services, entry):
    server = ControlServer(services)
    target = server.path.with_name("target")
    if entry == "symlink":
        target.write_text("do not touch")
        server.path.symlink_to(target)
    else:
        server.path.write_text("do not touch")
    before = server.path.lstat()

    with pytest.raises(PermissionError, match="non-socket or symlink"):
        asyncio.run(server.start())
    with pytest.raises(ControlProtocolError, match="non-socket or symlink"):
        control_request(server.path, {"action": "status"})

    assert server.path.lstat().st_ino == before.st_ino
    assert server.path.read_text() == "do not touch"
    if entry == "symlink":
        assert server.path.readlink() == target


def test_parent_symlink_is_refused(services, tmp_path, monkeypatch):
    alias = tmp_path / "alias"
    alias.symlink_to(services.settings.data_directory, target_is_directory=True)
    monkeypatch.setattr(services, "settings", Settings(data_directory=alias))
    server = ControlServer(services)
    with pytest.raises(PermissionError, match="symlink control directory"):
        asyncio.run(server.start())
    with pytest.raises(ControlProtocolError, match="symlink control directory"):
        control_request(server.path, {"action": "status"})
    assert alias.is_symlink()


def test_parent_writable_by_other_uids_is_refused(services):
    path = control_socket_path(services.settings)
    path.parent.chmod(0o770)
    with pytest.raises(PermissionError, match="control directory with unexpected owner"):
        asyncio.run(ControlServer(services).start())
    with pytest.raises(ControlProtocolError, match="control directory with unexpected owner"):
        control_request(path, {"action": "status"})
    assert not os.path.lexists(path)


def test_permissive_socket_is_refused_by_client(services):
    path = control_socket_path(services.settings)
    with TestClient(create_app(services=services, enable_control_socket=True)):
        path.chmod(0o644)
        with pytest.raises(ControlProtocolError, match="unexpected owner or permissions"):
            control_request(path, {"action": "status"})


def test_stale_socket_is_rebound_and_cleaned_up(services):
    path = control_socket_path(services.settings)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stale:
        _bind(stale, path)
    path.chmod(0o600)
    # Retain another name for the stale inode so the filesystem cannot recycle it.
    stale_path = path.with_name("stale.sock")
    os.link(path, stale_path)
    with pytest.raises(ControlUnavailableError):
        control_request(path, {"action": "status"})
    with TestClient(create_app(services=services, enable_control_socket=True)):
        assert not path.samefile(stale_path)
        assert control_request(path, {"action": "status"})["ok"] is True
    assert not os.path.lexists(path)
    assert stat.S_ISSOCK(stale_path.lstat().st_mode)


def test_shutdown_preserves_replacement_socket(services):
    path = control_socket_path(services.settings)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as replacement:
        with TestClient(create_app(services=services, enable_control_socket=True)):
            old = path.lstat()
            path.unlink()
            _bind(replacement, path)
            new = path.lstat()
            assert (new.st_dev, new.st_ino) != (old.st_dev, old.st_ino)
        assert path.lstat().st_ino == new.st_ino


def test_startup_preserves_an_existing_listener(services):
    async def scenario():
        server = ControlServer(services)
        await server.start()
        try:
            before = server.path.lstat()
            with pytest.raises(FileExistsError, match="already has a listener"):
                await ControlServer(services).start()
            assert server.path.lstat().st_ino == before.st_ino
            result = await asyncio.to_thread(control_request, server.path, {"action": "status"})
            assert result["ok"] is True
        finally:
            await server.close()

    asyncio.run(scenario())


def test_concurrent_start_cannot_unlink_socket_before_listen(services, monkeypatch):
    release_listen = threading.Event()
    original_listen = socket.socket.listen
    original_flock = control.fcntl.flock
    lock_attempts = 0

    async def scenario():
        loop = asyncio.get_running_loop()
        bound = asyncio.Event()
        second_lock_attempt = asyncio.Event()

        def pause_first_listen(sock, backlog):
            loop.call_soon_threadsafe(bound.set)
            assert release_listen.wait(timeout=2)
            original_listen(sock, backlog)

        def watch_flock(fd, operation):
            nonlocal lock_attempts
            lock_attempts += 1
            if lock_attempts == 2:
                loop.call_soon_threadsafe(second_lock_attempt.set)
            return original_flock(fd, operation)

        monkeypatch.setattr(socket.socket, "listen", pause_first_listen)
        monkeypatch.setattr(control.fcntl, "flock", watch_flock)
        first, second = ControlServer(services), ControlServer(services)
        first_start = asyncio.create_task(first.start())
        second_start = asyncio.create_task(second.start())
        try:
            await asyncio.wait_for(bound.wait(), 2)
            await asyncio.wait_for(second_lock_attempt.wait(), 2)
            assert stat.S_IMODE(first.path.with_suffix(".sock.lock").stat().st_mode) == 0o600
            assert not first_start.done() and not second_start.done()
            release_listen.set()
            done = await asyncio.gather(first_start, second_start, return_exceptions=True)
            assert sum(result is None for result in done) == 1
            assert sum(isinstance(result, FileExistsError) for result in done) == 1
            assert (await asyncio.to_thread(control_request, first.path, {"action": "status"}))[
                "ok"
            ] is True
        finally:
            release_listen.set()
            await asyncio.gather(first_start, second_start, return_exceptions=True)
            await asyncio.gather(first.close(), second.close())

    asyncio.run(scenario())


def test_handler_limit_and_shutdown_drain_idle_clients(services):
    async def scenario():
        all_peers_checked = asyncio.Event()
        checked = 0

        def peer_uid(sock):
            nonlocal checked
            checked += 1
            if checked == 8:
                all_peers_checked.set()
            return control._peer_uid(sock)

        server = ControlServer(services, peer_uid=peer_uid)
        await server.start()
        sockets = []
        try:
            for _ in range(8):
                sockets.append(_connect(server.path))
            await asyncio.wait_for(all_peers_checked.wait(), 2)
            with _connect(server.path) as overflow:
                assert await asyncio.to_thread(overflow.recv, 1) == b""
            await asyncio.wait_for(server.close(), 2)
            for sock in sockets:
                assert await asyncio.to_thread(sock.recv, 1) == b""
            assert not os.path.lexists(server.path)
        finally:
            await server.close()
            for sock in sockets:
                sock.close()

    asyncio.run(scenario())


def test_pair_waits_off_loop_and_shutdown_waits_for_its_worker(services, monkeypatch):
    monkeypatch.setattr(control, "_SHUTDOWN_GRACE", 0.01)

    async def scenario():
        server = ControlServer(services)
        loop = asyncio.get_running_loop()
        dispatch_started = asyncio.Event()
        real_dispatch = server._dispatch

        def observed_dispatch(request):
            loop.call_soon_threadsafe(dispatch_started.set)
            return real_dispatch(request)

        monkeypatch.setattr(server, "_dispatch", observed_dispatch)
        issue_pairing = Mock(wraps=services.auth.issue_pairing)
        monkeypatch.setattr(services.auth, "issue_pairing", issue_pairing)
        await server.start()
        try:
            barrier = services.registry.store.write_barrier
            with _connect(server.path) as sock, barrier:
                sock.sendall(b'{"action":"pair"}\n')
                await asyncio.wait_for(dispatch_started.wait(), 2)
                before = time.monotonic()
                await asyncio.sleep(0)
                assert time.monotonic() - before < 0.1
                issue_pairing.assert_not_called()
                shutdown = asyncio.create_task(server.close())
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(asyncio.shield(shutdown), 0.02)
                issue_pairing.assert_not_called()
            await asyncio.wait_for(shutdown, 2)
            issue_pairing.assert_called_once()
            assert not os.path.lexists(server.path)
        finally:
            await server.close()

    asyncio.run(scenario())


def test_long_control_path_works_for_bind_and_connect(tmp_path):
    settings = Settings(data_directory=tmp_path / ("deep-" * 30) / "data")
    path = control_socket_path(settings)
    assert len(os.fsencode(path)) > 120
    with TestClient(create_app(settings)):
        assert control_request(path, {"action": "status"})["data_directory"] == str(path.parent)
    assert not os.path.lexists(path)


@pytest.mark.parametrize(
    ("request_bytes", "error"),
    [
        (b"\n", "invalid_request"),
        (b'{"action":"status"}', "invalid_request"),
        (b'{"action":"status","value":"' + b"x" * 4096 + b'"}\n', "invalid_request"),
        (b"{?}\n", "invalid_request"),
        (b'{"key":"\xff"}\n', "invalid_request"),
        (b'["status"]\n', "invalid_request"),
        (b'{"action":NaN}\n', "invalid_request"),
        (b'{"action":"nope"}\n', "unknown_action"),
    ],
)
def test_invalid_requests_do_not_break_subsequent_requests(services, request_bytes, error):
    path = control_socket_path(services.settings)
    with TestClient(create_app(services=services, enable_control_socket=True)):
        assert _raw_request(path, request_bytes) == {"ok": False, "error": error}
        assert control_request(path, {"action": "status"})["ok"] is True
        # The maximum is for the JSON line itself, excluding the line terminator.
        exact_limit = b'{"action":"status"}'.ljust(4096) + b"\n"
        assert _raw_request(path, exact_limit)["ok"] is True


def test_unavailable_when_not_running(services):
    path = control_socket_path(services.settings)
    for missing in (path, path.parent / "missing" / path.name):
        with pytest.raises(ControlUnavailableError, match="control socket not found"):
            control_request(missing, {"action": "status"})


def test_client_rejects_oversized_request_before_connecting(services, monkeypatch):
    path = control_socket_path(services.settings)
    with TestClient(create_app(services=services, enable_control_socket=True)):
        connect = Mock(side_effect=AssertionError("oversized request attempted to connect"))
        monkeypatch.setattr(socket.socket, "connect", connect)
        with pytest.raises(ControlProtocolError, match="request exceeds size limit"):
            control_request(path, {"action": "status", "padding": "x" * 4096})
        connect.assert_not_called()


def test_server_replaces_oversized_response(services, monkeypatch):
    path = control_socket_path(services.settings)
    monkeypatch.setattr(ControlServer, "_dispatch", lambda *_: {"data": "x" * 16384})
    with TestClient(create_app(services=services, enable_control_socket=True)):
        assert control_request(path, {"action": "status"}) == {
            "ok": False,
            "error": "response_too_large",
        }


@pytest.mark.parametrize("blocked", ["drain", "wait_closed"])
def test_stalled_client_transport_is_aborted_on_write_deadline(services, monkeypatch, blocked):
    async def scenario():
        aborted = asyncio.Event()

        async def stall(writer):
            abort = writer.transport.abort

            def observed_abort():
                aborted.set()
                abort()

            monkeypatch.setattr(writer.transport, "abort", observed_abort)
            await asyncio.Future()

        # Force actual stream operations to stall without depending on kernel
        # socket buffer sizes or a particular platform's backpressure thresholds.
        monkeypatch.setattr(asyncio.StreamWriter, blocked, stall)
        server = ControlServer(services, write_timeout=0.02)
        await server.start()
        try:
            with _connect(server.path) as sock:
                sock.sendall(b'{"action":"status"}\n')
                await asyncio.wait_for(aborted.wait(), 1)
                await asyncio.wait_for(server.close(), 1)
            assert not os.path.lexists(server.path)
        finally:
            await server.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("registry_type", ["rollback", "wal", "unmigrated", "missing", "corrupt"])
def test_status_cannot_migrate_or_write_registry(services, tmp_path, monkeypatch, registry_type):
    path = control_socket_path(services.settings)
    registry = tmp_path / "registry ?#.sqlite3"
    if registry_type == "corrupt":
        registry.write_bytes(b"not SQLite")
    elif registry_type != "missing":
        with closing(sqlite3.connect(registry)) as connection:
            if registry_type == "wal":
                connection.execute("PRAGMA journal_mode=WAL")
            if registry_type != "unmigrated":
                connection.execute("CREATE TABLE records (namespace, id, body)")
                connection.execute(
                    "INSERT INTO records VALUES ('registry','active',?)",
                    (json.dumps({"project_id": "project-legacy"}),),
                )
                connection.commit()
            else:
                connection.execute("CREATE TABLE unrelated (value)")
    if registry.exists():
        registry.chmod(0o400)
    before = registry.read_bytes() if registry.exists() else None
    contents_before = set(tmp_path.iterdir())
    migrate = Mock(side_effect=AssertionError("status attempted to migrate the registry"))
    with TestClient(create_app(services=services, enable_control_socket=True)):
        monkeypatch.setattr(services.registry.store, "path", registry)
        monkeypatch.setattr(Store, "migrate", migrate)
        result = control_request(path, {"action": "status"})
        if registry_type in {"rollback", "wal"}:
            assert result["ok"] is True
            assert result["active_project_id"] == "project-legacy"
        else:
            assert result == {"ok": False, "error": "status_unavailable"}
    assert (registry.read_bytes() if registry.exists() else None) == before
    assert set(tmp_path.iterdir()) == contents_before  # Includes no new WAL/SHM or backup files.
    migrate.assert_not_called()


@pytest.mark.parametrize("locking_mode", ["NORMAL", "EXCLUSIVE"])
def test_status_reads_committed_active_binding_from_live_wal(services, monkeypatch, locking_mode):
    path = control_socket_path(services.settings)
    registry = services.registry.store.path

    def source_state():
        return {
            item.name: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
            for item in registry.parent.iterdir()
            for info in (item.lstat(),)
        }

    with services.registry.store.transaction() as tx:
        tx.put("registry", "active", {"project_id": "project-checkpointed"})
    migrate = Mock(side_effect=AssertionError("status attempted to migrate the registry"))
    with TestClient(create_app(services=services, enable_control_socket=True)):
        monkeypatch.setattr(Store, "migrate", migrate)
        assert control_request(path, {"action": "status"})["active_project_id"] == (
            "project-checkpointed"
        )
        with closing(sqlite3.connect(registry)) as connection:
            connection.execute(f"PRAGMA locking_mode={locking_mode}")
            connection.execute(
                "UPDATE records SET body=? WHERE namespace='registry' AND id='active'",
                (json.dumps({"project_id": "project-live-only-in-wal"}),),
            )
            connection.commit()
            wal = registry.with_name(registry.name + "-wal")
            assert wal.stat().st_size > 0
            shm = registry.with_name(registry.name + "-shm")
            assert shm.exists() is (locking_mode == "NORMAL")
            before = source_state()
            directory_mtime = registry.parent.stat().st_mtime_ns
            result = control_request(path, {"action": "status"})
            assert result["ok"] is True
            assert result["active_project_id"] == "project-live-only-in-wal"
            assert source_state() == before
            assert registry.parent.stat().st_mtime_ns == directory_mtime
    migrate.assert_not_called()


def test_status_refuses_nonempty_rollback_journal(services, tmp_path, monkeypatch):
    path = control_socket_path(services.settings)
    registry = tmp_path / "rollback.sqlite3"
    with closing(sqlite3.connect(registry)) as connection:
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("CREATE TABLE records (namespace, id, body)")
        connection.execute(
            "INSERT INTO records VALUES ('registry','active',?)",
            (json.dumps({"project_id": "project-checkpointed"}),),
        )
        connection.commit()
        connection.execute(
            "UPDATE records SET body=? WHERE namespace='registry' AND id='active'",
            (json.dumps({"project_id": "project-uncommitted"}),),
        )
        journal = registry.with_name(registry.name + "-journal")
        assert journal.stat().st_size > 0
        with TestClient(create_app(services=services, enable_control_socket=True)):
            monkeypatch.setattr(services.registry.store, "path", registry)
            assert control_request(path, {"action": "status"}) == {
                "ok": False,
                "error": "status_unavailable",
            }


def test_client_authenticates_server_before_sending(services, monkeypatch):
    path = control_socket_path(services.settings)

    async def scenario():
        received = asyncio.get_running_loop().create_future()

        async def receive(reader, writer):
            try:
                received.set_result(await reader.read(128))
            finally:
                writer.close()
                await writer.wait_closed()

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            _bind(sock, path)
            path.chmod(0o600)
            fake = await asyncio.start_unix_server(receive, sock=sock)
            async with fake:
                monkeypatch.setattr(control, "_peer_uid", lambda _: os.getuid() + 1)
                with pytest.raises(ControlProtocolError, match="server UID"):
                    await asyncio.to_thread(control_request, path, {"action": "status"})
                assert await asyncio.wait_for(received, 2) == b""

    asyncio.run(scenario())


@pytest.mark.parametrize("response", [b"nonsense\n", b"[]\n", b"{}", b"x" * 16385 + b"\n"])
def test_client_rejects_invalid_responses(services, response):
    path = control_socket_path(services.settings)

    async def reply(reader, writer):
        try:
            await reader.readuntil(b"\n")
            writer.write(response)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async def scenario():
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            _bind(sock, path)
            path.chmod(0o600)
            fake = await asyncio.start_unix_server(reply, sock=sock)
            async with fake:
                with pytest.raises(ControlProtocolError):
                    await asyncio.to_thread(control_request, path, {"action": "status"})

    asyncio.run(scenario())
