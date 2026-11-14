import fcntl
import os
from pathlib import Path
from uuid import uuid4

from proscenium.api.errors import DomainError


class Ownership:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.file = path.open("a+")
        path.chmod(0o600)
        try:
            fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.file.close()
            raise DomainError("owner_conflict", 409) from exc
        self.epoch = f"epoch-{uuid4()}"
        self.pid = os.getpid()
        self.process_start = Path(f"/proc/{self.pid}/stat").read_text().split(") ")[1].split()[19]
        self.file.seek(0)
        self.file.truncate()
        self.file.write(f"{self.epoch} {self.pid} {self.process_start}\n")
        self.file.flush()

    def close(self) -> None:
        if not self.file.closed:
            fcntl.flock(self.file, fcntl.LOCK_UN)
            self.file.close()
