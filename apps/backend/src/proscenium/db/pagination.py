import base64
import hashlib
import hmac
import json
from typing import Any

from proscenium.api.errors import DomainError
from proscenium.contracts import canonical


class Cursors:
    def __init__(self, secret: bytes):
        self.secret = secret

    def encode(self, scope: str, after: str) -> str:
        data = canonical({"scope": scope, "after": after})
        signature = hmac.digest(self.secret, data, hashlib.sha256)
        return base64.urlsafe_b64encode(signature + data).decode().rstrip("=")

    def decode(self, scope: str, cursor: str | None) -> str:
        if cursor is None:
            return ""
        try:
            raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
            signature, data = raw[:32], raw[32:]
            if not hmac.compare_digest(signature, hmac.digest(self.secret, data, hashlib.sha256)):
                raise ValueError()
            payload = json.loads(data)
            if payload["scope"] != scope or not isinstance(payload["after"], str):
                raise ValueError()
            return payload["after"]
        except (ValueError, KeyError, TypeError) as exc:
            raise DomainError("invalid_cursor") from exc

    def page(
        self,
        scope: str,
        records: list[dict[str, Any]],
        key: str,
        *,
        cursor: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        if not 1 <= limit <= 100:
            raise DomainError("invalid_cursor")
        after = self.decode(scope, cursor)
        remaining = sorted((r for r in records if r[key] > after), key=lambda r: r[key])
        selected = remaining[:limit]
        next_cursor = self.encode(scope, selected[-1][key]) if len(remaining) > limit else None
        return dict(schema_version="1.0", items=selected, next_cursor=next_cursor)
