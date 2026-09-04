"""Unit tests for the mounted MCP server: vocabulary, auth-scope gating, item
shaping and tool/prompt registration (guards against import/wiring regressions)."""

import os

# The MCP module builds the FastMCP app (and reads settings) at import time;
# ensure the always-required settings exist so this no-DB unit module imports.
for _k in ("POSTGRESQL_PASSWORD", "SHOPIFY_HMAC_SECRET", "SECRET_KEY"):
    os.environ.setdefault(_k, "test")

import asyncio  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402
from fastmcp.exceptions import ToolError  # noqa: E402

from app.mcp import server as mcp_server  # noqa: E402
from app.mcp.context import (  # noqa: E402
    get_session_school,
    require_principal,
    require_scope,
    require_write_scope,
    resolve_school,
    set_session_school,
)
from app.mcp.server import _collection_item_brief  # noqa: E402
from app.mcp.vocabulary import vocabulary  # noqa: E402


def test_vocabulary_sourced_from_enums():
    vocab = vocabulary()
    assert "SPOT" in vocab["reading_abilities"]
    assert "HARRY_POTTER" in vocab["reading_abilities"]
    assert vocab["hues"] and all(isinstance(h, str) for h in vocab["hues"])


def test_require_write_scope_denies_without_scope():
    ctx = SimpleNamespace(scopes={"catalogue:read"})
    with pytest.raises(ToolError):
        require_write_scope(ctx, "books:label")


def test_require_write_scope_allows_with_scope():
    ctx = SimpleNamespace(scopes={"books:label", "catalogue:read"})
    require_write_scope(ctx, "books:label")  # no raise


def test_require_scope_gates_reads():
    ctx = SimpleNamespace(scopes={"offline_access"})
    with pytest.raises(ToolError):
        require_scope(ctx, "catalogue:read", "search")
    require_scope(
        SimpleNamespace(scopes={"catalogue:read"}), "catalogue:read", "search"
    )  # no raise


def test_resolve_school_precedence():
    assert (
        resolve_school(
            requested="A",
            session_default="B",
            token_default="C",
            authorized={"A"},
            is_admin=False,
        )
        == "A"
    )
    assert (
        resolve_school(
            requested=None,
            session_default="B",
            token_default="C",
            authorized={"B"},
            is_admin=False,
        )
        == "B"
    )
    assert (
        resolve_school(
            requested=None,
            session_default=None,
            token_default="C",
            authorized={"C"},
            is_admin=False,
        )
        == "C"
    )


def test_resolve_school_non_admin_confined_to_authorized_set():
    with pytest.raises(ToolError):
        resolve_school(
            requested="OTHER",
            session_default=None,
            token_default="C",
            authorized={"C"},
            is_admin=False,
        )


def test_resolve_school_admin_may_target_any():
    assert (
        resolve_school(
            requested="ANY",
            session_default=None,
            token_default="C",
            authorized=set(),
            is_admin=True,
        )
        == "ANY"
    )


def test_resolve_school_requires_a_target():
    with pytest.raises(ToolError):
        resolve_school(
            requested=None,
            session_default=None,
            token_default=None,
            authorized=set(),
            is_admin=True,
        )


@pytest.mark.asyncio
async def test_session_school_keyed_by_grant_not_user(monkeypatch):
    from key_value.aio.stores.memory import MemoryStore

    from app.mcp import context

    storage = MemoryStore()
    monkeypatch.setattr(context, "get_mcp_storage", lambda: storage)
    await set_session_school("grant-1", "school-A")
    await set_session_school("grant-2", "school-B")
    assert await get_session_school("grant-1") == "school-A"
    assert await get_session_school("grant-2") == "school-B"
    assert await get_session_school(None) is None


@pytest.mark.asyncio
async def test_session_school_storage_failure_does_not_fall_back(monkeypatch):
    from unittest.mock import AsyncMock

    from app.mcp import context

    storage = SimpleNamespace(get=AsyncMock(side_effect=RuntimeError("unavailable")))
    monkeypatch.setattr(context, "get_mcp_storage", lambda: storage)
    with pytest.raises(RuntimeError, match="unavailable"):
        await get_session_school("grant-1")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"primary_hue": "made-up"},
        {"secondary_hue": "made-up"},
        {"reading_ability": "made-up"},
        {"min_age": -1},
        {"min_age": 12, "max_age": 8},
        {"max_age": 19},
    ],
)
async def test_invalid_labels_are_rejected_before_mutation(changes):
    from fastmcp import Client

    arguments = {
        "work_id": 123,
        "primary_hue": vocabulary()["hues"][0],
        "min_age": 5,
        "max_age": 8,
        "reading_ability": "SPOT",
        **changes,
    }
    async with Client(mcp_server.mcp) as client:
        result = await client.call_tool("label_book", arguments, raise_on_error=False)
    assert result.is_error
    assert "Not authenticated" not in str(result.content)


def test_require_principal_enforces_confined_membership():
    # A plain educator (no schooladmin) is denied a schooladmin-only action.
    ctx = SimpleNamespace(principals=["educator:12", "role:educator"])
    with pytest.raises(ToolError):
        require_principal(ctx, "schooladmin:12", action="import books")
    require_principal(
        SimpleNamespace(principals=["schooladmin:12"]),
        "schooladmin:12",
        action="import books",
    )  # no raise


def test_collection_item_brief_prefers_held_edition_cover():
    item = SimpleNamespace(
        edition=SimpleNamespace(
            isbn="9780000000001",
            title="A Held Edition",
            cover_url="https://img/held.jpg",
        ),
        edition_isbn="9780000000001",
        work=SimpleNamespace(title="The Work"),
        copies_total=3,
    )
    brief = _collection_item_brief(item)
    assert brief["isbn"] == "9780000000001"
    assert brief["cover_url"] == "https://img/held.jpg"
    assert brief["copies_total"] == 3


def test_collection_item_brief_drops_none_fields():
    item = SimpleNamespace(
        edition=SimpleNamespace(isbn=None, title=None, cover_url=None),
        edition_isbn=None,
        work=None,
        copies_total=None,
    )
    assert _collection_item_brief(item) == {}


def test_all_tools_and_prompts_registered():
    tools = {t.name for t in asyncio.run(mcp_server.mcp.list_tools())}
    prompts = {p.name for p in asyncio.run(mcp_server.mcp.list_prompts())}
    assert tools == {
        "whoami",
        "list_my_schools",
        "use_school",
        "list_label_vocabulary",
        "search_books",
        "get_book",
        "get_recommendations",
        "get_collection",
        "import_books",
        "label_book",
    }
    assert prompts == {
        "research_and_label_book",
        "build_reading_list",
        "import_from_isbn_list",
    }


@pytest.mark.asyncio
async def test_http_transport_does_not_require_instance_local_sessions():
    import httpx

    app = mcp_server.http_app
    async with app.lifespan(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/mcp",
                headers={"Accept": "application/json, text/event-stream"},
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1"},
                    },
                },
            )
            assert response.status_code == 200
            assert "mcp-session-id" not in response.headers
            response = await client.post(
                "/mcp",
                headers={"Accept": "application/json, text/event-stream"},
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/list",
                    "params": {},
                },
            )
            assert response.status_code == 200
            assert "import_books" in response.text


@pytest.mark.asyncio
async def test_oauth_requires_client_consent_before_school_picker(monkeypatch):
    import base64
    import hashlib
    from urllib.parse import parse_qs, urlparse

    import httpx
    from fastmcp import FastMCP
    from key_value.aio.stores.memory import MemoryStore

    from app.mcp import storage

    monkeypatch.setattr(mcp_server.settings, "MCP_ENABLED", True)
    monkeypatch.setattr(
        mcp_server.settings,
        "OAUTH_MCP_CLIENT_SECRET",
        "local-test-client-secret-at-least-32-characters",
    )
    monkeypatch.setattr(storage, "get_mcp_storage", lambda: MemoryStore())
    app = FastMCP("consent-test", auth=mcp_server._build_auth()).http_app()
    origin = mcp_server.settings.MCP_BASE_URL
    async with app.lifespan(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url=origin
        ) as client:
            registered = await client.post(
                "/register",
                json={
                    "client_name": "Test Library Client",
                    "redirect_uris": ["http://localhost:9999/callback"],
                    "token_endpoint_auth_method": "none",
                    "grant_types": ["authorization_code", "refresh_token"],
                    "response_types": ["code"],
                },
            )
            assert registered.status_code == 201, registered.text
            challenge = (
                base64.urlsafe_b64encode(hashlib.sha256(b"v" * 43).digest())
                .rstrip(b"=")
                .decode()
            )
            response = await client.get(
                "/authorize",
                params={
                    "client_id": registered.json()["client_id"],
                    "redirect_uri": "http://localhost:9999/callback",
                    "response_type": "code",
                    "scope": "catalogue:read",
                    "state": "test-state",
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                },
            )
            assert response.status_code in (302, 303, 307), response.text
            destination = urlparse(response.headers["location"])
            assert destination.path == "/consent"
            consent = await client.get(response.headers["location"])
            assert consent.status_code == 200
            assert "Test Library Client" in consent.text
            transaction = parse_qs(destination.query)["txn_id"][0]
            callback = await client.get(
                "/auth/callback", params={"state": transaction, "code": "unbound-code"}
            )
            assert callback.status_code >= 400
