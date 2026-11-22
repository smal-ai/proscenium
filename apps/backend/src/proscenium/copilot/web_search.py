"""Read-only public web discovery through GitHub Copilot's fixed MCP endpoint."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import shutil
import socket
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlsplit

import httpx

from proscenium.api.errors import DomainError
from proscenium.configuration import Settings
from proscenium.contracts import validate

MCP_READONLY_ENDPOINT = "https://api.githubcopilot.com/mcp/readonly"
MAX_RESPONSE_BYTES = 1_048_576
AUTH_UNAVAILABLE = "Web search authentication is unavailable"
SERVICE_UNAVAILABLE = "Web search service is unavailable"
INVALID_QUERY = "Web search query must be a non-empty string up to 512 characters"
_UNSAFE_URL = re.compile(r"[\x00-\x1f\x7f-\x9f\ud800-\udfff\s\\]")
_CONTROLS = re.compile(r"[\x00-\x1f\x7f-\x9f\ud800-\udfff]")
_CITATION = re.compile(r"\[([^\[\]\r\n]*)\]\((https?://[^\s()]+)\)")
_BEARER_TOKEN = re.compile(r"[A-Za-z0-9._~+/-]+=*")


async def resolve_copilot_token(settings: Settings) -> str:
    token = settings.secret_env("GH_TOKEN") or settings.secret_env("GITHUB_TOKEN")
    if token is None and (gh_bin := shutil.which("gh")) is not None:
        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                gh_bin,
                "auth",
                "token",
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=5.0)
            if process.returncode == 0:
                token = stdout.decode("ascii").strip()
        except (OSError, TimeoutError, UnicodeError):
            pass
        finally:
            if process is not None and process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()
    if token and _BEARER_TOKEN.fullmatch(token):
        return token
    raise DomainError("capability_unavailable", 501, AUTH_UNAVAILABLE)


def _load_json(value: str | bytes) -> Any:
    try:
        return json.loads(value)
    except (ValueError, RecursionError):
        return None


def _sse_messages(body: str) -> Iterator[Any]:
    data: list[str] = []
    # SSE frames use CR, LF or CRLF, not every Unicode separator recognised by splitlines().
    for line in [*re.split(r"\r\n|\r|\n", body), ""]:
        if line.startswith("data:"):
            data.append(line[5:].removeprefix(" "))
        elif not line and data:
            message = _load_json("\n".join(data))
            if message is not None:
                yield message
            elif len(data) > 1:
                # Some providers emit one JSON message per data line without a blank separator.
                yield from map(_load_json, data)
            data.clear()
        elif line.startswith("{") and not data:
            yield _load_json(line)


def _rpc_result(body: bytes, content_type: str, request_id: int) -> dict[str, Any]:
    if content_type.split(";", 1)[0].strip().lower() == "text/event-stream":
        messages = _sse_messages(body.decode("utf-8-sig", errors="replace"))
    else:
        message = _load_json(body)
        if message is None:
            messages = map(_load_json, body.splitlines())
        else:
            messages = iter(message if isinstance(message, list) else [message])
    for message in messages:
        if (
            not isinstance(message, dict)
            or type(message.get("id")) is not int
            or message["id"] != request_id
        ):
            continue
        result = message.get("result")
        if (
            "error" not in message
            and isinstance(result, dict)
            and result.get("isError") is not True
        ):
            return result
        break
    raise DomainError("provider_unavailable", 503, SERVICE_UNAVAILABLE)


async def _rpc(
    client: httpx.AsyncClient, headers: dict[str, str], request_id: int, method: str, params: dict
) -> tuple[dict[str, Any], str | None]:
    try:
        async with client.stream(
            "POST",
            MCP_READONLY_ENDPOINT,
            headers=headers,
            json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
        ) as response:
            if response.status_code in {401, 403}:
                raise DomainError("capability_unavailable", 501, AUTH_UNAVAILABLE)
            if response.status_code == 200:
                encodings = [
                    value.strip().lower()
                    for header in response.headers.get_list("content-encoding")
                    for value in header.split(",")
                    if value.strip()
                ]
                if any(encoding != "identity" for encoding in encodings):
                    raise DomainError("provider_unavailable", 503, SERVICE_UNAVAILABLE)
                body = bytearray()
                async for chunk in response.aiter_raw():
                    if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise DomainError("provider_unavailable", 503, SERVICE_UNAVAILABLE)
                    body.extend(chunk)
                return (
                    _rpc_result(bytes(body), response.headers.get("content-type", ""), request_id),
                    response.headers.get("Mcp-Session-Id"),
                )
    except (httpx.HTTPError, UnicodeError):
        pass
    raise DomainError("provider_unavailable", 503, SERVICE_UNAVAILABLE)


def _clean_public_url(raw: Any) -> str | None:
    if not isinstance(raw, str) or not 8 <= len(raw) <= 2048 or _UNSAFE_URL.search(raw):
        return None
    try:
        raw.encode("utf-8")
        parsed = urlsplit(raw)
        # Reading port also rejects malformed/out-of-range ports.
        _ = parsed.port
        host = (parsed.hostname or "").encode("idna").decode("ascii").lower().rstrip(".")
    except (ValueError, UnicodeError):
        return None
    if (
        parsed.scheme not in {"http", "https"}
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or "%" in host
        or host == "localhost"
        or host.endswith((".localhost", ".local"))
    ):
        return None
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # Browsers also interpret shortened, octal, hex and integer IPv4 forms as literals.
        try:
            ip = ipaddress.IPv4Address(socket.inet_aton(host))
        except OSError:
            ip = None
    if ip is not None and (not ip.is_global or ip.is_multicast):
        return None
    # Match the wire contract's lower-case scheme but preserve the source path and query.
    return parsed.scheme + raw[raw.index(":") :]


def _clean_text(value: Any, limit: int) -> str:
    return _CONTROLS.sub("", value).strip()[:limit].rstrip() if isinstance(value, str) else ""


def _result_items(result: dict[str, Any]) -> Iterator[dict[str, Any]]:
    content = result.get("content")
    if not isinstance(content, list):
        raise DomainError("provider_unavailable", 503, SERVICE_UNAVAILABLE)
    structured = False
    markdown = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        text = block.get("text")
        if not isinstance(text, str):
            continue
        payload = _load_json(text)
        if not isinstance(payload, dict) or "search_result_groups" not in payload:
            markdown.append(text)
            continue
        structured = True
        groups = payload["search_result_groups"]
        if isinstance(groups, list):
            for group in groups:
                if isinstance(group, dict) and isinstance(group.get("items"), list):
                    yield from (item for item in group["items"] if isinstance(item, dict))
    if not structured:
        for text in markdown:
            for title, url in _CITATION.findall(text):
                yield {"title": title, "url": url}


def _hits(result: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    hits = []
    seen = set()
    for item in _result_items(result):
        if (url := _clean_public_url(item.get("url"))) is None:
            continue
        parsed = urlsplit(url)
        assert parsed.hostname is not None
        default_port = 443 if parsed.scheme == "https" else 80
        identity = (
            parsed.scheme,
            parsed.hostname.encode("idna").decode("ascii").lower().rstrip("."),
            parsed.port or default_port,
            parsed.path or "/",
            parsed.query,
        )
        if identity in seen:
            continue
        seen.add(identity)
        snippets = []
        raw_snippets = item.get("snippets")
        if isinstance(raw_snippets, list):
            for value in raw_snippets:
                if snippet := _clean_text(value, 600):
                    snippets.append(snippet)
                    if len(snippets) == 3:
                        break
        hits.append(
            {
                "title": _clean_text(item.get("title"), 512) or parsed.hostname,
                "url": url,
                "snippets": snippets,
                "published_at": _clean_text(item.get("last_updated_date"), 64) or None,
            }
        )
        if len(hits) == limit:
            break
    return hits


def clean_search_query(query: Any) -> str:
    if not isinstance(query, str) or len(query) > 512 or not (clean := query.strip()):
        raise DomainError("invalid_request", 422, INVALID_QUERY)
    return clean


async def search_web(
    services: Any,
    project_id: str,
    *,
    query: str,
    limit: int = 8,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    clean_query = clean_search_query(query)
    if type(limit) is not int or not 1 <= limit <= 15:
        raise DomainError("invalid_request")
    token = await resolve_copilot_token(services.settings)
    transport = transport or getattr(services, "web_search_transport", None)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Accept-Encoding": "identity",
        "X-MCP-Readonly": "true",
        "X-MCP-Tools": "web_search",
    }
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(15.0, connect=5.0),
        follow_redirects=False,
        transport=transport,
        trust_env=False,
    ) as client:
        _, session_id = await _rpc(
            client,
            headers,
            1,
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "proscenium", "version": "0.1.0"},
            },
        )
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        result, _ = await _rpc(
            client,
            headers,
            2,
            "tools/call",
            {"name": "web_search", "arguments": {"query": clean_query}},
        )
    return validate(
        "WebSearchResult",
        {
            "schema_version": "1.0",
            "project_id": project_id,
            "query": clean_query,
            "results": _hits(result, limit),
        },
    )
