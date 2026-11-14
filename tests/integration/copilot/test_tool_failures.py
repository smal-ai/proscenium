import asyncio
import json

from proscenium.api.errors import DomainError
from proscenium.copilot.tools import Tools, tool_failure


def test_preview_failure_preserves_code_and_safe_recipe_not_private_details():
    result = tool_failure(
        DomainError(
            "capability_unavailable",
            501,
            "This installed profile is Cycles CPU. No GPU fallback is permitted",
            headers={"Authorization": "Bearer DO_NOT_EXPOSE"},
            cli_path="/private/runtime",
            native_exception="RuntimeError('DO_NOT_EXPOSE')",
        ),
        "request_preview",
    )
    body = json.loads(result.text_result_for_llm)
    assert body["error"] == "capability_unavailable"
    assert body["message"] == "This installed profile is Cycles CPU. No GPU fallback is permitted"
    assert body["details"]["renderer_revision"] == "proscenium-blender-1"
    assert body["details"]["device"] == "CPU"
    assert "DO_NOT_EXPOSE" not in result.text_result_for_llm
    assert "/private" not in result.text_result_for_llm


def test_unknown_error_message_and_details_are_not_forwarded():
    result = tool_failure(
        DomainError(
            "source_conflict",
            409,
            "Upstream Authorization: Bearer SECRET /home/private/.env",
            current_revision=4,
            nested={"token": "SECRET"},
            reason="SECRET",
        ),
        "publish_outputs",
    )
    body = json.loads(result.text_result_for_llm)
    assert body["error"] == "source_conflict"
    assert "pinned item/version" in body["message"]
    assert body["details"] == {"current_revision": 4}
    assert "SECRET" not in result.text_result_for_llm


def test_unexpected_service_exception_never_leaks_repr():
    class BrokenTools(Tools):
        def caller(self, invocation, *, admit=True):
            raise RuntimeError("provider headers Authorization: Bearer SECRET")

    result = asyncio.run(BrokenTools(None, None, "project").invoke("get_job", None))
    body = json.loads(result.text_result_for_llm)
    assert body["error"] == "internal_error"
    assert "original durable receipt" in body["message"]
    assert "SECRET" not in result.text_result_for_llm
