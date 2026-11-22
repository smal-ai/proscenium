"""Hermetic coverage of web discovery protocol, sanitisation and invocation authority."""

import asyncio
import hashlib
import json
import traceback
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from copilot import CopilotClient
from copilot.tools import ToolInvocation
from proscenium.api.errors import DomainError
from proscenium.configuration import Settings
from proscenium.contracts import canonical, validate
from proscenium.copilot import web_search
from proscenium.copilot.outcomes import get_tool_outcome
from proscenium.copilot.service import Copilot
from proscenium.copilot.state import key, save
from proscenium.copilot.tools import Tools, read_arguments
from test_activity_feedback import BINDING, PROJECT, SESSION, TOOL, WORK, event
from test_activity_feedback import runtime as activity_runtime
from test_request_targeting import context

TOKEN = "ghp_synthetic_never_print_or_persist"
NAME = "search_web"
QUERY = {"query": "  public architecture references  "}
SOURCE = "https://example.com/references?source=original"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("Real subprocess, native model or HTTP execution is forbidden")

    monkeypatch.setenv("GH_TOKEN", TOKEN)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(CopilotClient, "start", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    monkeypatch.setattr(web_search.shutil, "which", lambda name: None)


def envelope(identity, **data):
    return {"jsonrpc": "2.0", "id": identity, **data}


def structured(*items):
    return {"type": "text", "text": json.dumps({"search_result_groups": [{"items": items}]})}


class RawBody(httpx.AsyncByteStream):
    def __init__(self, content):
        self.content = content

    async def __aiter__(self):
        yield self.content


def mock_transport(*content, initialise=None, call=None):
    requests = []

    def handler(request):
        requests.append(request)
        identity = json.loads(request.content)["id"]
        if identity == 1:
            response = initialise or httpx.Response(200, json=envelope(1, result={}))
        else:
            assert identity == 2
            response = call or httpx.Response(200, json=envelope(2, result={"content": content}))
        # Byte/JSON convenience responses are pre-read by HTTPX's constructor. Rewrap
        # them as transport responses so the adapter exercises the real raw-stream path.
        if response.is_stream_consumed:
            return httpx.Response(
                response.status_code, headers=response.headers, stream=RawBody(response.content)
            )
        return response

    return httpx.MockTransport(handler), requests


def search(transport, **kwargs):
    services = SimpleNamespace(settings=Settings())
    return asyncio.run(web_search.search_web(services, PROJECT, transport=transport, **kwargs))


def assert_safe(error, code, status, message):
    assert (error.code, error.status, error.message, error.details) == (code, status, message, {})
    assert TOKEN not in "".join(traceback.format_exception(error))
    assert error.__context__ is None


def test_sse_json_rpc_matches_request_and_forwards_read_only_session():
    init = httpx.Response(
        200,
        headers={
            "Content-Type": "text/event-stream; charset=utf-8",
            "Mcp-Session-Id": "test-session",
        },
        content=(
            b"\xef\xbb\xbf: heartbeat\r\nevent: message\r\n"
            b'data: {"id": 99, "error": {}}\r\n\r\n'
            b'data: {"jsonrpc": "2.0",\r\ndata: "id": 1, "result": {}}\r\n\r\n'
        ),
    )
    result = envelope(2, result={"content": [structured({"title": "Source", "url": SOURCE})]})
    call = httpx.Response(
        200,
        headers={"Content-Type": "text/event-stream"},
        text=f'data: {{"id": true, "result": {{}}}}\ndata: {json.dumps(result)}\n\n',
    )
    transport, requests = mock_transport(initialise=init, call=call)
    assert search(transport, **QUERY) == {
        "schema_version": "1.0",
        "project_id": PROJECT,
        "query": QUERY["query"].strip(),
        "results": [{"title": "Source", "url": SOURCE, "snippets": [], "published_at": None}],
    }
    for request in requests:
        assert str(request.url) == "https://api.githubcopilot.com/mcp/readonly"
        assert request.method == "POST"
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        assert request.headers["Content-Type"] == "application/json"
        assert request.headers["Accept"] == "application/json, text/event-stream"
        assert request.headers["X-MCP-Readonly"] == "true"
        assert request.headers["X-MCP-Tools"] == "web_search"
        assert request.extensions["timeout"] == dict(connect=5.0, read=15.0, write=15.0, pool=15.0)
    assert "Mcp-Session-Id" not in requests[0].headers
    assert requests[1].headers["Mcp-Session-Id"] == "test-session"
    assert json.loads(requests[0].content) == envelope(
        1,
        method="initialize",
        params={
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "proscenium", "version": "0.1.0"},
        },
    )
    assert json.loads(requests[1].content) == envelope(
        2,
        method="tools/call",
        params={"name": "web_search", "arguments": {"query": QUERY["query"].strip()}},
    )


@pytest.mark.parametrize("content_type", ["application/json", "text/event-stream"])
def test_json_lines_and_markdown_fallback(content_type):
    markdown = {"type": "text", "text": f"- [Reference]({SOURCE})\n[Unsafe](http://localhost/foo)"}
    body = "\n".join(
        json.dumps(message)
        for message in [envelope(9, error={}), envelope(2, result={"content": [markdown]})]
    )
    transport, requests = mock_transport(
        call=httpx.Response(200, headers={"Content-Type": content_type}, text=body)
    )
    assert search(transport, **QUERY)["results"] == [
        {"title": "Reference", "url": SOURCE, "snippets": [], "published_at": None}
    ]
    assert "Mcp-Session-Id" not in requests[1].headers
    transport, _ = mock_transport(markdown, structured({"url": "file:///tmp/test"}))
    assert search(transport, **QUERY)["results"] == []


@pytest.mark.parametrize(
    "url",
    [
        None,
        123,
        "http://",
        "javascript:alert(1)",
        "file:///tmp/private",
        "//example.com/path",
        "https://user@example.com/",
        "https://user:pass@example.com/",
        "https://@example.com/",
        "https://localhost./",
        "https://a.LOCALHOST/",
        "https://machine.local/",
        "https://localhost。/",
        "https://%6cocalhost/",
        "http://127.0.0.1/",
        "http://127.1/",
        "http://0x7f000001/",
        "http://2130706433/",
        "http://0177.0.0.1/",
        "http://10.0.0.1/",
        "http://172.16.0.1/",
        "http://192.168.1.1/",
        "http://169.254.169.254/",
        "http://0.0.0.0/",
        "http://224.0.0.1/",
        "http://[::1]/",
        "http://[::]/",
        "http://[fd00::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://[fe80::1%25en0]/",
        "http://[ff02::1]/",
        "https://example.com:65536/",
        "https://example.com:wrong/",
        "https://[::1/",
        "https://exam ple.com/",
        "https://example.com/a\t",
        "https://example.com/\x7f",
        "https://example.com/a\u00a0b",
        "https://example.com\\path",
        "https://example.com/" + "a" * 2048,
    ],
)
def test_rejects_unsafe_urls(url):
    assert web_search._clean_public_url(url) is None


@pytest.mark.parametrize(
    "template",
    [
        "https://exa{}mple.com/",
        "https://example.com/p{}ath",
        "https://example.com/?q={}",
        "https://example.com/#{}",
    ],
    ids=["host", "path", "query", "fragment"],
)
def test_rejects_c1_controls_and_unpaired_surrogates_in_every_url_component(template):
    for char in [*(chr(value) for value in range(0x80, 0xA0)), "\ud800", "\udfff"]:
        url = template.format(char)
        assert web_search._clean_public_url(url) is None, f"U+{ord(char):04X}"
        with pytest.raises(DomainError, match="Invalid WebSearchHit"):
            validate(
                "WebSearchHit",
                {"title": "Example", "url": url, "snippets": [], "published_at": None},
            )


def test_web_search_hit_contract_rejects_backslashes():
    with pytest.raises(DomainError, match="Invalid WebSearchHit"):
        validate(
            "WebSearchHit",
            {
                "title": "Example",
                "url": "https://example.com\\path",
                "snippets": [],
                "published_at": None,
            },
        )


@pytest.mark.parametrize("url", ["http://8.8.8.8/", "https://[2606:4700:4700::1111]/", SOURCE])
def test_accepts_public_urls(url):
    assert web_search._clean_public_url(url) == url


def test_structured_results_bound_deduplicate_and_keep_exact_source():
    url = "https://EXAMPLE.com:443/a?rank=1#citation"
    transport, _ = mock_transport(
        {"type": "image", "text": f"[Not text]({SOURCE})"},
        structured(
            {"url": "http://169.254.169.254/"},
            {
                "url": url,
                "title": "\x00" + "a" * 600,
                "snippets": ["\x00 ", None, "b" * 650, "\x7f c", " d\x85", "excluded"],
                "last_updated_date": " x" * 40,
            },
            {"url": "https://example.com/a?rank=1#duplicate"},
            {"url": SOURCE, "title": "\u0000", "last_updated_date": 42},
            {"url": "https://example.org/beyond-limit"},
        ),
    )
    result = search(transport, limit=2, **QUERY)
    assert validate("WebSearchResult", result) == result
    first, second = result["results"]
    assert first == {
        "url": url,
        "title": "a" * 512,
        "snippets": ["b" * 600, "c", "d"],
        "published_at": ("x " * 32).rstrip(),
    }
    assert second == {"url": SOURCE, "title": "example.com", "snippets": [], "published_at": None}


@pytest.mark.parametrize("status", [401, 403, 201, 302, 429, 503])
@pytest.mark.parametrize("step", ["initialise", "call"])
def test_http_errors_are_safe_and_never_redirect(status, step):
    response = httpx.Response(status, content=TOKEN, headers={"Location": "https://other.example/"})
    transport, requests = mock_transport(**{step: response})
    with pytest.raises(DomainError) as raised:
        search(transport, **QUERY)
    if status in {401, 403}:
        assert_safe(raised.value, "capability_unavailable", 501, web_search.AUTH_UNAVAILABLE)
    else:
        assert_safe(raised.value, "provider_unavailable", 503, web_search.SERVICE_UNAVAILABLE)
    assert len(requests) == (1 if step == "initialise" else 2)


@pytest.mark.parametrize(
    "response",
    [
        envelope(2, error={"message": TOKEN}),
        envelope(2, result={"isError": True, "content": [{"type": "text", "text": TOKEN}]}),
        envelope(1, result={"content": []}),
        envelope(2, result={"content": TOKEN}),
        TOKEN,
    ],
)
def test_invalid_rpc_and_tool_failures_are_safe(response):
    transport, _ = mock_transport(call=httpx.Response(200, json=response))
    with pytest.raises(DomainError) as raised:
        search(transport, **QUERY)
    assert_safe(raised.value, "provider_unavailable", 503, web_search.SERVICE_UNAVAILABLE)


def test_network_errors_are_safe(caplog):
    def handler(request):
        raise httpx.ReadTimeout(f"Sensitive upstream diagnostics: {TOKEN}", request=request)

    with pytest.raises(DomainError) as raised:
        search(httpx.MockTransport(handler), **QUERY)
    assert_safe(raised.value, "provider_unavailable", 503, web_search.SERVICE_UNAVAILABLE)
    assert TOKEN not in caplog.text


@pytest.mark.parametrize("step", ["initialise", "call"])
@pytest.mark.parametrize("encoding", ["gzip", "br", "identity, gzip"])
def test_non_identity_encoding_is_rejected_before_reading(step, encoding):
    class Unreadable(httpx.AsyncByteStream):
        closed = False

        def __aiter__(self):
            pytest.fail("Adapter read a response with a non-identity encoding")

        async def aclose(self):
            self.closed = True

    stream = Unreadable()
    response = httpx.Response(
        200,
        headers=[("Content-Encoding", " IDENTITY "), ("Content-Encoding", encoding)],
        stream=stream,
    )
    transport, requests = mock_transport(**{step: response})
    with pytest.raises(DomainError) as raised:
        search(transport, **QUERY)
    assert_safe(raised.value, "provider_unavailable", 503, web_search.SERVICE_UNAVAILABLE)
    assert stream.closed
    assert len(requests) == (1 if step == "initialise" else 2)


@pytest.mark.parametrize("step", ["initialise", "call"])
def test_raw_response_cap_closes_the_stream_without_reading_more(step):
    class Oversized(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield b" " * web_search.MAX_RESPONSE_BYTES
            yield b"!"
            pytest.fail("Reader continued after exceeding its cap")

        async def aclose(self):
            self.closed = True

    stream = Oversized()
    response = httpx.Response(200, headers={"Content-Encoding": "IDENTITY"}, stream=stream)
    transport, requests = mock_transport(**{step: response})
    with pytest.raises(DomainError) as raised:
        search(transport, **QUERY)
    assert_safe(raised.value, "provider_unavailable", 503, web_search.SERVICE_UNAVAILABLE)
    assert stream.closed
    assert len(requests) == (1 if step == "initialise" else 2)


def test_environment_token_precedence(monkeypatch):
    settings = Settings()
    monkeypatch.setenv("GITHUB_TOKEN", "second-choice")
    assert asyncio.run(web_search.resolve_copilot_token(settings)) == TOKEN
    monkeypatch.delenv("GH_TOKEN")
    assert asyncio.run(web_search.resolve_copilot_token(settings)) == "second-choice"


@pytest.mark.parametrize("value", [None, "bad\r\nInjected: header", " "])
def test_missing_or_malformed_credentials_fail_before_transport(monkeypatch, value):
    monkeypatch.delenv("GH_TOKEN")
    if value is not None:
        monkeypatch.setenv("GH_TOKEN", value)
    transport, requests = mock_transport()
    with pytest.raises(DomainError) as raised:
        search(transport, **QUERY)
    assert_safe(raised.value, "capability_unavailable", 501, web_search.AUTH_UNAVAILABLE)
    assert requests == []


@pytest.mark.parametrize(
    "status,output,accepted",
    [(0, b"machine-token\n", True), (0, b"\n", False), (1, TOKEN.encode(), False)],
)
def test_cli_token_result(monkeypatch, status, output, accepted):
    monkeypatch.delenv("GH_TOKEN")
    monkeypatch.setattr(
        web_search.shutil, "which", lambda name: "/fixture/gh" if name == "gh" else None
    )
    spawn = AsyncMock(
        return_value=SimpleNamespace(
            returncode=status, communicate=AsyncMock(return_value=(output, None))
        )
    )
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    if accepted:
        assert asyncio.run(web_search.resolve_copilot_token(Settings())) == "machine-token"
    else:
        with pytest.raises(DomainError) as raised:
            asyncio.run(web_search.resolve_copilot_token(Settings()))
        assert_safe(raised.value, "capability_unavailable", 501, web_search.AUTH_UNAVAILABLE)
    spawn.assert_awaited_once_with(
        "/fixture/gh",
        "auth",
        "token",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )


@pytest.mark.parametrize("cancelled", [False, True])
def test_cli_subprocess_killed_and_reaped_on_timeout_or_cancellation(monkeypatch, cancelled):
    monkeypatch.delenv("GH_TOKEN")
    monkeypatch.setattr(web_search.shutil, "which", lambda name: "/fixture/gh")
    calls = []

    async def run():
        started = asyncio.Event()

        async def communicate():
            started.set()
            if cancelled:
                await asyncio.Future()
            raise TimeoutError(TOKEN)

        async def wait():
            calls.append("wait")

        process = SimpleNamespace(
            returncode=None, communicate=communicate, kill=lambda: calls.append("kill"), wait=wait
        )
        monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
        actual_wait_for = asyncio.wait_for

        async def expected_timeout(awaitable, timeout):
            assert timeout == 5.0
            return await actual_wait_for(awaitable, timeout=timeout)

        monkeypatch.setattr(asyncio, "wait_for", expected_timeout)
        task = asyncio.create_task(web_search.resolve_copilot_token(Settings()))
        await started.wait()
        if cancelled:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(DomainError) as raised:
                await task
            assert_safe(raised.value, "capability_unavailable", 501, web_search.AUTH_UNAVAILABLE)

    asyncio.run(run())
    assert calls == ["kill", "wait"]


INVOCATION = key(SESSION, "tool", TOOL)


@pytest.fixture
def runtime(tmp_path):
    state, store, gate = activity_runtime.__wrapped__(tmp_path)
    services = state.services
    services.settings = Settings(
        runtime={"worker_model": "fixture"}, data_directory=tmp_path / "machine"
    )
    services.extensions = {"providers": SimpleNamespace(options={})}
    services.extension = services.extensions.__getitem__
    with store.transaction() as tx:
        state.bind(tx, SESSION, "tool", TOOL, BINDING)
        save(tx, "copilot.control", "gate", {"fenced": False, "epoch": services.ownership.epoch})
        tx.put(
            "invocations",
            INVOCATION,
            {
                **BINDING,
                "invocation_id": INVOCATION,
                "native_id": TOOL,
                "session_id": SESSION,
                "agent_id": None,
                "owner_epoch": services.ownership.epoch,
                "state": "running",
                "kind": "tool",
                "name": NAME,
                "arguments_sha256": hashlib.sha256(canonical(QUERY)).hexdigest(),
            },
        )
    return Tools(services, state, PROJECT), store, gate


def invoke(tools, name=NAME, arguments=None):
    invocation = ToolInvocation(
        session_id=SESSION,
        tool_call_id=TOOL,
        tool_name=name,
        arguments=QUERY if arguments is None else arguments,
        agent_id=None,
    )
    result = asyncio.run(tools.invoke(name, invocation))
    return result.result_type, json.loads(result.text_result_for_llm)


def set_work_state(tx, state):
    work = tx.require("work", WORK)
    save(tx, "work", WORK, {**work, "state": state, "state_revision": work["state_revision"] + 1})


@pytest.mark.parametrize("scope", ["default", "planning_only"])
def test_active_work_can_search_with_no_project_mutation(runtime, scope):
    tools, store, _ = runtime
    with store.transaction() as tx:
        tx.put("contexts", "context-one", context(execution_scope=scope), schema="ContextSnapshot")
    tools.services.web_search_transport, requests = mock_transport(structured({"url": SOURCE}))
    assert NAME in {tool.name for tool in tools.definitions()}
    kind, result = invoke(tools)
    assert kind == "success"
    assert validate("WebSearchResult", result)["results"][0]["url"] == SOURCE
    assert len(requests) == 2
    with store.transaction(write=False) as tx:
        assert get_tool_outcome(tx, INVOCATION) is None
        assert tx.all("artifacts") == tx.all("jobs") == []


@pytest.mark.parametrize(
    "reason", ["fenced", "stop_requested", "stopped", "stale_epoch", "closed_gate"]
)
def test_fenced_stopped_or_stale_invocations_never_reach_transport(runtime, monkeypatch, reason):
    tools, store, gate = runtime
    transport, requests = mock_transport()
    tools.services.web_search_transport = transport
    auth = AsyncMock(return_value=TOKEN)
    monkeypatch.setattr(web_search, "resolve_copilot_token", auth)
    with store.transaction() as tx:
        if reason == "fenced":
            save(
                tx,
                "copilot.control",
                "gate",
                {"fenced": True, "epoch": tools.services.ownership.epoch},
            )
        elif reason in {"stop_requested", "stopped"}:
            set_work_state(tx, reason)
        elif reason == "stale_epoch":
            save(
                tx,
                "invocations",
                INVOCATION,
                {**tx.require("invocations", INVOCATION), "owner_epoch": "previous"},
            )
        else:
            gate.close(PROJECT)
    kind, result = invoke(tools)
    assert kind == "failure"
    assert result["error"] == ("project_busy" if reason == "closed_gate" else "permission_denied")
    assert requests == []
    auth.assert_not_awaited()


def test_capabilities_report_defaults_and_exact_overrides(runtime):
    tools, _, _ = runtime
    kind, result = invoke(tools, "get_project_capabilities", {})
    assert kind == "success"
    names = {NAME, "web_fetch"}
    assert names <= set(result["exposed_tools"])
    assert names.isdisjoint(result["missing_worker_tools"])
    tools.services.settings = replace(
        tools.services.settings, runtime={"worker_model": "fixture", "worker_tools": ["view"]}
    )
    kind, result = invoke(tools, "get_project_capabilities", {})
    assert kind == "success"
    assert result["exposed_tools"] == ["view"]
    assert names <= set(result["missing_worker_tools"])


def test_authoring_specialist_web_fetch_url_permission_is_correlated_and_auto_approved(runtime):
    tools, store, _ = runtime
    state = tools.state
    child_id, call_id, permission_id = "research-child", "web-fetch-call", "web-fetch-permission"
    request = {
        "kind": "url",
        "toolCallId": call_id,
        "url": SOURCE,
        "intention": "Read the public reference",
    }

    child_start = event("research-start", "subagent.started", agentName="authoring-specialist")
    state.reduce(PROJECT, SESSION, {**child_start, "agentId": child_id})

    def child_event(identity, kind, **data):
        state.reduce(
            PROJECT,
            SESSION,
            {**event(identity, kind, **data), "agentId": child_id, "parentId": child_start["id"]},
        )

    # The native URL permission may arrive before web_fetch's tool start event.
    child_event(
        "permission-request",
        "permission.requested",
        requestId=permission_id,
        permissionRequest=request,
    )
    copilot = Copilot.__new__(Copilot)
    copilot.services, copilot.state, copilot.closing = tools.services, state, False
    decision = asyncio.run(copilot.permission(PROJECT, request, {"session_id": SESSION}))
    assert decision.kind == "approve-once"

    child_event(
        "fetch-start",
        "tool.execution_start",
        toolCallId=call_id,
        toolName="web_fetch",
        arguments={"url": SOURCE},
    )
    child_event(
        "permission-done",
        "permission.completed",
        requestId=permission_id,
        toolCallId=call_id,
        result={"kind": "approved"},
    )
    with store.transaction(write=False) as tx:
        child = tx.require("invocations", key(SESSION, "agent", child_id))
        assert (child["name"], child["work_id"], child["state"]) == (
            "authoring-specialist",
            WORK,
            "running",
        )
        call = tx.require("invocations", key(SESSION, "tool", call_id))
        assert (call["agent_id"], call["name"], call["work_id"]) == (child_id, "web_fetch", WORK)
        stage = tx.require("copilot.permissions", key(SESSION, permission_id))
        assert stage["invocation_id"] == call["invocation_id"]
        assert stage["fingerprint"] == hashlib.sha256(canonical(request)).hexdigest()
        assert (stage["phase"], stage["authority"], stage["reply"]) == (
            "completed",
            "automatic_policy",
            "approve",
        )
        assert state.control(tx)["fenced"] is False
        assert tx.require("work", WORK)["state"] == "running"
        assert tx.all("decisions") == []


def test_safe_tool_failure_and_persisted_diagnostic(runtime):
    tools, store, _ = runtime
    tools.services.web_search_transport, _ = mock_transport(
        call=httpx.Response(200, json=envelope(2, error={"message": TOKEN}))
    )
    kind, result = invoke(tools)
    assert kind == "failure"
    assert result == {
        "error": "provider_unavailable",
        "message": web_search.SERVICE_UNAVAILABLE,
        "details": {},
    }
    with store.transaction(write=False) as tx:
        diagnostic = get_tool_outcome(tx, INVOCATION)
        assert diagnostic["safe_summary"] == web_search.SERVICE_UNAVAILABLE
        assert TOKEN not in json.dumps(diagnostic)


@pytest.mark.parametrize(
    "args",
    [
        {},
        {"query": "  "},
        {"query": None},
        {"query": "x" * 513},
        {"query": "x", "limit": True},
        {"query": "x", "limit": 16},
        {"query": "x", "limit": 0},
        {"query": "x", "endpoint": "https://example.com"},
    ],
)
def test_bad_arguments_are_rejected(args):
    with pytest.raises(DomainError, match="Web search query|invalid request"):
        read_arguments(NAME, args)


def test_cold_stop_settles_departed_invoked_search_as_read_only(runtime):
    tools, store, _ = runtime
    call = {"toolCallId": TOOL, "toolName": NAME, "arguments": QUERY}
    history = [
        {"id": "start", "type": "tool.execution_start", "data": call},
        {"id": "requested", "type": "external_tool.requested", "data": call},
    ]
    departed = {
        "session_id": SESSION,
        "pid": 12345,
        "start": "old-start",
        "observed_identity": None,
        "invocation_ids": [INVOCATION],
        "pending_empty": True,
        "current_send": False,
    }
    with store.transaction() as tx:
        set_work_state(tx, "stop_requested")
        assert tools.state.interrupt_departed_custom_tools(tx, SESSION, history, departed) == 1
        invocation = tx.require("invocations", INVOCATION)
        assert invocation["state"] == "cancelled"
        assert invocation["recovery"]["kind"] == "departed_read_only_custom_tool_interrupted"
        assert invocation["recovery"]["native_return_observed"] is False
        assert tools.state.interrupt_departed_custom_tools(tx, SESSION, history, departed) == 0
