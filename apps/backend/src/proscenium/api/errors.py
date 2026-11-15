from typing import Any


class DomainError(Exception):
    def __init__(self, code: str, status: int = 400, message: str | None = None, **details: Any):
        self.code, self.status = code, status
        self.message = message or code.replace("_", " ")
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
