import errno
import fcntl
import os
import stat
from pathlib import Path
from uuid import uuid4

from proscenium.api.errors import DomainError


class Ownership:
    def __init__(self, path: Path, *, dir_fd: int | None = None):
        if dir_fd is None:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
        try:
            fd = os.open(path if dir_fd is None else path.name, flags, 0o600, dir_fd=dir_fd)
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.EISDIR}:
                raise DomainError("scope_denied", 403) from exc
            raise
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise DomainError("scope_denied", 403)
            os.fchmod(fd, 0o600)
            self.file = os.fdopen(fd, "r+", encoding="utf-8")
        except BaseException:
            os.close(fd)
            raise
        try:
            try:
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise DomainError("owner_conflict", 409) from exc
            self.epoch = f"epoch-{uuid4()}"
            self.pid = os.getpid()
            self.process_start = (
                Path(f"/proc/{self.pid}/stat").read_text().split(") ")[1].split()[19]
            )
            self.file.seek(0)
            self.file.truncate()
            self.file.write(f"{self.epoch} {self.pid} {self.process_start}\n")
            self.file.flush()
        except BaseException:
            self.file.close()
            raise

    def close(self) -> None:
        if not self.file.closed:
            fcntl.flock(self.file, fcntl.LOCK_UN)
            self.file.close()
