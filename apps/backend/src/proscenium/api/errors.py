from typing import Any


class DomainError(Exception):
    def __init__(
        self,
        code: str,
        status: int = 400,
        message: str | None = None,
        *,
        safe_for_model: bool = False,
        **details: Any,
    ):
        self.code, self.status = code, status
        self.message = message or code.replace("_", " ")
        # Opt in only with fixed host-authored text. This flag is not part of the HTTP envelope.
        self.safe_for_model = bool(safe_for_model and message is not None)
        self.details = details
        super().__init__(self.message)

    def envelope(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "error": {
                "code": self.code,
                "message": self.message,
                "operation_id": None,
                "retryable": False,
                "details": self.details,
            },
        }
