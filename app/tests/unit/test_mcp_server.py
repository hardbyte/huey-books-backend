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
    require_principal,
    require_scope,
    require_write_scope,
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
