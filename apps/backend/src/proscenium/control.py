"""Linux owner-only control channel for a live Proscenium process.

Cooperating instances serialise registry ownership with owner.lock and socket
lifecycle changes with control.sock.lock. Arbitrary same-UID processes share
the OS user's discretionary filesystem authority and need not honour locks.
"""

import asyncio
import errno
import fcntl
import grp
import inspect
import json
import os
import pwd
import socket
import sqlite3
import stat
import struct
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Coroutine, Iterator
from contextlib import closing, contextmanager, suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn
from urllib.parse import quote

from proscenium.api.errors import DomainError
from proscenium.contracts import canonical

if TYPE_CHECKING:
    from proscenium.configuration import Settings
    from proscenium.services import Services

_REQUEST_LIMIT = 4096
_RESPONSE_LIMIT = 16 * 1024
_REQUEST_TIMEOUT = 5.0
_WRITE_TIMEOUT = 5.0
_SHUTDOWN_GRACE = 6.0
_CLIENT_LIMIT = 8


class ControlUnavailableError(Exception):
    """The control socket could not be reached or did not respond in time."""


class ControlProtocolError(Exception):
    """The control endpoint failed a safety or protocol check."""


def control_socket_path(settings: "Settings") -> Path:
    return settings.data_directory / "control.sock"


def _open_parent(path: Path) -> int:
    return _open_validated_directory(path.parent)


def _trusted_sticky(info: os.stat_result) -> bool:
    return bool(info.st_mode & stat.S_ISVTX) and info.st_uid in (0, os.getuid())


def _is_exclusive_user_group(uid: int, gid: int) -> bool:
    if uid != os.getuid() or gid != os.getgid():
        return False
    try:
        user = pwd.getpwuid(uid)
        group = grp.getgrgid(gid)
        return (
            group.gr_name == user.pw_name
            and set(group.gr_mem) <= {user.pw_name}
            and not any(entry.pw_gid == gid and entry.pw_uid != uid for entry in pwd.getpwall())
        )
    except (KeyError, OSError):
        return False


def _check_directory(fd: int, *, leaf: bool, allow_private_group: bool) -> None:
    info = os.fstat(fd)
    trusted_uids = (os.getuid(),) if leaf else (0, os.getuid())
    if (
        info.st_uid in trusted_uids
        and not info.st_mode & 0o002
        and (
            not info.st_mode & 0o020
            or (allow_private_group and _is_exclusive_user_group(info.st_uid, info.st_gid))
        )
    ):
        return
    if not leaf and _trusted_sticky(info):
        return
    raise PermissionError("Refusing control directory with unexpected owner or permissions")


def _open_validated_directory(
    path: Path,
    *,
    create: bool = False,
    trusted_leaf: bool = False,
    allow_private_group: bool = False,
) -> int:
    """Open a pinned directory, optionally treating its leaf as a trusted ancestor."""
    parts = path.absolute().parts[1:]
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        _check_directory(
            fd, leaf=not parts and not trusted_leaf, allow_private_group=allow_private_group
        )
        for idx, part in enumerate(parts):
            created = False
            try:
                try:
                    next_fd = os.open(part, flags, dir_fd=fd)
                except FileNotFoundError:
                    if not create:
                        raise
                    # A cooperating starter may create this component first.
                    with suppress(FileExistsError):
                        os.mkdir(part, 0o700, dir_fd=fd)
                        created = True
                    next_fd = os.open(part, flags, dir_fd=fd)
            except OSError as exc:
                if exc.errno in (errno.ENOTDIR, errno.ELOOP):
                    raise PermissionError(
                        "Refusing non-directory or symlink control directory"
                    ) from exc
                raise
            os.close(fd)
            fd = next_fd
            _check_directory(
                fd,
                leaf=created or (idx == len(parts) - 1 and not trusted_leaf),
                allow_private_group=allow_private_group,
            )
        return fd
    except BaseException:
        os.close(fd)
        raise


def validate_control_directory(path: Path, *, create: bool = False) -> None:
    """Reject unsafe paths before startup creates the registry or ownership lock."""
    os.close(_open_validated_directory(path, create=create))


@contextmanager
def _locked_socket_directory(path: Path) -> Iterator[int]:
    fd = _open_parent(path)
    try:
        try:
            lock = os.open(
                "control.sock.lock",
                os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                0o600,
                dir_fd=fd,
            )
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise PermissionError("Refusing symlink control socket lock") from exc
            raise
        try:
            info = os.fstat(lock)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
                raise PermissionError("Refusing unsafe control socket lock")
            os.fchmod(lock, 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield fd
        finally:
            # Keep the inode in place so all cooperating callers lock the same file.
            os.close(lock)
    finally:
        os.close(fd)


def _socket_address(fd: int, name: str) -> str:
    # Pin the parent and keep its potentially long absolute path out of sun_path.
    return f"/proc/self/fd/{fd}/{name}"


def _peer_uid(sock: socket.socket) -> int:
    credentials = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    _, uid, _ = struct.unpack("3i", credentials)
    return uid


def _reject_json_constant(value: str) -> NoReturn:
    raise ValueError("Non-JSON numeric constant")


def _parse_object(line: bytes) -> dict[str, Any]:
    value = json.loads(line.decode("utf-8"), parse_constant=_reject_json_constant)
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


def _unlink_matching_socket(fd: int, name: str, identity: tuple[int, int]) -> None:
    with suppress(FileNotFoundError):
        info = os.lstat(name, dir_fd=fd)
        if stat.S_ISSOCK(info.st_mode) and (info.st_dev, info.st_ino) == identity:
            os.unlink(name, dir_fd=fd)


def _read_active_project_id(registry_path: Path) -> str | None:
    with suppress(FileNotFoundError):
        journal = registry_path.with_name(registry_path.name + "-journal")
        if journal.lstat().st_size > 0:
            raise sqlite3.OperationalError("Registry has pending rollback journal data")

    wal = registry_path.with_name(registry_path.name + "-wal")
    has_wal_frames = False
    with suppress(FileNotFoundError):
        info = wal.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise sqlite3.OperationalError("Registry WAL is not a regular file")
        has_wal_frames = info.st_size > 0
    if not has_wal_frames:
        return _query_active_project_id(registry_path, immutable=True)

    # A normal SQLite WAL reader may create an SHM file, even in mode=ro.
    # Copy under the service write barrier and let SQLite touch only the copy.
    # Use a private directory outside the source, even if TMPDIR points into it.
    with tempfile.TemporaryDirectory(prefix="proscenium-status-", dir="/tmp") as directory:
        snapshot = Path(directory) / registry_path.name
        # Closing a raw descriptor in the server process could drop its SQLite
        # POSIX locks. Keep the source file opens in a bounded child process.
        try:
            subprocess.run(
                [
                    sys.executable,
                    "-IS",
                    str(Path(__file__).with_name("_control_snapshot.py")),
                    str(registry_path.absolute()),
                    str(snapshot),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3.0,
                check=True,
            )
        except subprocess.SubprocessError as exc:
            raise sqlite3.OperationalError("Registry WAL snapshot unavailable") from exc
        return _query_active_project_id(snapshot, immutable=False)


def _query_active_project_id(path: Path, *, immutable: bool) -> str | None:
    uri = f"file:{quote(str(path.absolute()))}?mode=ro"
    if immutable:
        uri += "&immutable=1"
    with closing(sqlite3.connect(uri, uri=True, timeout=1.0)) as connection:
        connection.execute("PRAGMA query_only = ON")
        row = connection.execute(
            "SELECT body FROM records WHERE namespace='registry' AND id='active'"
        ).fetchone()
    if row is not None:
        if not isinstance(row[0], str):
            raise ValueError("Invalid registry body")
        project_id = _parse_object(row[0].encode("utf-8")).get("project_id")
        if isinstance(project_id, str):
            return project_id
    return None


async def _finish(operation: Coroutine[Any, Any, None]) -> None:
    """Defer cancellation until resource acquisition or release has settled."""
    task = asyncio.create_task(operation)
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            with suppress(asyncio.CancelledError):
                await asyncio.shield(task)
        task.result()
        raise


class ControlServer:
    def __init__(
        self,
        services: "Services",
        *,
        peer_uid: Callable[[socket.socket], int] = _peer_uid,
        write_timeout: float = _WRITE_TIMEOUT,
    ) -> None:
        self.services = services
        self.path = control_socket_path(services.settings)
        self._peer_uid = peer_uid
        self._write_timeout = write_timeout
        self._lifecycle = asyncio.Lock()
        self._server: asyncio.Server | None = None
        self._identity: tuple[int, int] | None = None
        self._clients: dict[asyncio.Task[None], asyncio.StreamWriter] = {}

    async def start(self) -> None:
        async with self._lifecycle:
            if self._server is not None:
                raise RuntimeError("Control server already started")
            try:
                await _finish(self._start())
            except asyncio.CancelledError:
                await _finish(self._close())
                raise

    async def _start(self) -> None:
        sock, identity = await asyncio.to_thread(self._bind_socket)
        try:
            # Python 3.13+ can unlink sockets itself. Only our inode guard may do that.
            options: dict[str, Any] = {}
            if (
                "cleanup_socket"
                in inspect.signature(asyncio.get_running_loop().create_unix_server).parameters
            ):
                options["cleanup_socket"] = False
            self._server = await asyncio.start_unix_server(
                self._handle_client, sock=sock, limit=_REQUEST_LIMIT, backlog=16, **options
            )
            self._identity = identity
        except BaseException:
            sock.close()
            await asyncio.to_thread(self._remove_socket, identity)
            raise

    def _bind_socket(self) -> tuple[socket.socket, tuple[int, int]]:
        name = self.path.name
        with _locked_socket_directory(self.path) as fd:
            address = _socket_address(fd, name)
            try:
                existing = os.lstat(name, dir_fd=fd)
            except FileNotFoundError:
                pass
            else:
                if not stat.S_ISSOCK(existing.st_mode):
                    raise PermissionError("Refusing non-socket or symlink control path")
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                    probe.settimeout(0.1)
                    try:
                        probe.connect(address)
                    except ConnectionRefusedError:
                        _unlink_matching_socket(fd, name, (existing.st_dev, existing.st_ino))
                    else:
                        raise FileExistsError("Proscenium control socket already has a listener")

            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            identity = None
            try:
                sock.bind(address)
                bound = os.lstat(name, dir_fd=fd)
                identity = (bound.st_dev, bound.st_ino)
                os.chmod(name, 0o600, dir_fd=fd)
                sock.listen(16)
                return sock, identity
            except BaseException:
                sock.close()
                if identity is not None:
                    _unlink_matching_socket(fd, name, identity)
                raise

    def _remove_socket(self, identity: tuple[int, int]) -> None:
        with suppress(FileNotFoundError, PermissionError):
            with _locked_socket_directory(self.path) as fd:
                _unlink_matching_socket(fd, self.path.name, identity)

    def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        # Authenticate before reading or allowing the peer to occupy a handler slot.
        try:
            uid = self._peer_uid(writer.get_extra_info("socket"))
        except OSError:
            writer.close()
            return
        if uid != os.getuid() or self._server is None or len(self._clients) >= _CLIENT_LIMIT:
            writer.close()
            return
        task = asyncio.create_task(self._respond(reader, writer))
        self._clients[task] = writer
        task.add_done_callback(self._clients.pop)

    async def _respond(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            try:
                line = await asyncio.wait_for(reader.readuntil(b"\n"), _REQUEST_TIMEOUT)
                request = _parse_object(line[:-1])
            except (
                TimeoutError,
                asyncio.IncompleteReadError,
                asyncio.LimitOverrunError,
                ValueError,
                RecursionError,
            ):
                response = {"ok": False, "error": "invalid_request"}
            else:
                if self._server is None:
                    return
                response = await asyncio.to_thread(self._dispatch, request)
            if self._server is None:
                return
            encoded = canonical(response) + b"\n"
            if len(encoded) > _RESPONSE_LIMIT:
                encoded = canonical({"ok": False, "error": "response_too_large"}) + b"\n"
            writer.write(encoded)
            await asyncio.wait_for(writer.drain(), self._write_timeout)
        except (OSError, TimeoutError):
            self._abort(writer)
        finally:
            try:
                writer.close()
                await asyncio.wait_for(writer.wait_closed(), self._write_timeout)
            except (OSError, TimeoutError):
                self._abort(writer)

    @staticmethod
    def _abort(writer: asyncio.StreamWriter) -> None:
        if writer.transport is not None:
            writer.transport.abort()

    def _dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        settings = self.services.settings
        if request.get("action") == "pair":
            # The store takes this same reentrant barrier. Bound admission so
            # waiting behind another service write cannot hang shutdown.
            if not self.services.activation_lock.acquire(timeout=_REQUEST_TIMEOUT):
                return {"ok": False, "error": "pairing_unavailable"}
            try:
                code = self.services.auth.issue_pairing()
            except (DomainError, OSError, sqlite3.Error):
                return {"ok": False, "error": "pairing_unavailable"}
            finally:
                self.services.activation_lock.release()
            return {
                "ok": True,
                "pairing_url": f"{settings.origin}/#pair={code}",
                "expires_in_seconds": 900,
            }
        if request.get("action") == "status":
            frontend = settings.frontend_directory
            store = self.services.registry.store
            if not self.services.activation_lock.acquire(timeout=1.0):
                return {"ok": False, "error": "status_unavailable"}
            try:
                active = _read_active_project_id(store.path)
            except (OSError, sqlite3.Error, ValueError, RecursionError):
                return {"ok": False, "error": "status_unavailable"}
            finally:
                self.services.activation_lock.release()
            return {
                "ok": True,
                "pid": os.getpid(),
                "origin": settings.origin,
                "data_directory": str(settings.data_directory),
                "active_project_id": active,
                "owner_epoch": self.services.ownership.epoch,
                "frontend_ready": frontend is not None and (frontend / "index.html").is_file(),
            }
        return {"ok": False, "error": "unknown_action"}

    async def close(self) -> None:
        async with self._lifecycle:
            await _finish(self._close())

    async def _close(self) -> None:
        server = self._server
        if server is None:
            return
        self._server = None
        server.close()
        try:
            clients = self._clients.copy()
            for writer in clients.values():
                writer.close()
            if clients:
                _, pending = await asyncio.wait(clients, timeout=_SHUTDOWN_GRACE)
                for task in pending:
                    self._abort(clients[task])
                # Cancelling a to_thread waiter does not stop a running SQLite
                # operation. Keep owner.lock held until all such work settles.
                await asyncio.gather(*clients, return_exceptions=True)
            await server.wait_closed()
        finally:
            identity, self._identity = self._identity, None
            if identity is not None:
                await asyncio.to_thread(self._remove_socket, identity)


def _read_response(sock: socket.socket, deadline: float) -> dict[str, Any]:
    line = bytearray()
    while len(line) < _RESPONSE_LIMIT:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        sock.settimeout(remaining)
        chunk = sock.recv(min(4096, _RESPONSE_LIMIT - len(line)))
        if not chunk:
            break
        data, separator, _ = chunk.partition(b"\n")
        line.extend(data)
        if separator:
            try:
                return _parse_object(bytes(line))
            except (ValueError, RecursionError):
                raise ControlProtocolError("Malformed Proscenium control response") from None
    raise ControlProtocolError("Incomplete or oversized Proscenium control response")


def control_request(
    socket_path: Path, payload: dict[str, Any], *, timeout: float = 5.0
) -> dict[str, Any]:
    encoded = canonical(payload) + b"\n"
    if len(encoded) > _REQUEST_LIMIT:
        raise ControlProtocolError("Control request exceeds size limit")
    try:
        fd = _open_parent(socket_path)
    except FileNotFoundError as exc:
        raise ControlUnavailableError("Proscenium control socket not found") from exc
    except PermissionError as exc:
        raise ControlProtocolError(str(exc)) from exc
    try:
        try:
            info = os.lstat(socket_path.name, dir_fd=fd)
        except FileNotFoundError as exc:
            raise ControlUnavailableError("Proscenium control socket not found") from exc
        if not stat.S_ISSOCK(info.st_mode):
            raise ControlProtocolError("Refusing non-socket or symlink control path")
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ControlProtocolError(
                "Refusing control socket with unexpected owner or permissions"
            )

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            deadline = time.monotonic() + timeout
            sock.settimeout(timeout)
            try:
                sock.connect(_socket_address(fd, socket_path.name))
                if _peer_uid(sock) != os.getuid():
                    raise ControlProtocolError("Unexpected Proscenium control server UID")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                sock.settimeout(remaining)
                sock.sendall(encoded)
                return _read_response(sock, deadline)
            except (FileNotFoundError, ConnectionError, TimeoutError) as exc:
                raise ControlUnavailableError("Proscenium control socket unavailable") from exc
    finally:
        os.close(fd)
