import base64
import hashlib
import hmac
import json
from collections.abc import Callable
from typing import Any

from proscenium.api.errors import DomainError
from proscenium.contracts import canonical, validate_stored_json


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

    def stored_page(
        self,
        scope: str,
        rows: list[tuple[str, dict[str, Any]]],
        key: str | Callable[[dict[str, Any]], str],
        record_schema: str,
        *,
        cursor: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Retain each record's exact bytes until the selected page has been validated."""
        keyed = []
        for raw, record in rows:
            try:
                order = record[key] if isinstance(key, str) else key(record)
            except (KeyError, TypeError, ValueError, AttributeError, OverflowError) as exc:
                raise DomainError("invalid_request", message=f"Invalid {record_schema}") from exc
            if not isinstance(order, str):
                raise DomainError("invalid_request", message=f"Invalid {record_schema}")
            keyed.append(dict(order=order, raw=raw, record=record))
        page = self.page(scope, keyed, "order", cursor=cursor, limit=limit)
        page["items"] = [
            validate_stored_json(record_schema, row["raw"], row["record"]) for row in page["items"]
        ]
        return page
