import uuid

import pytest
from sqlalchemy import select, text

from app.models.collection import Collection
from app.models.collection_item import CollectionItem
from app.schemas.collection import CollectionItemCreateIn
from app.services.collections import add_editions_to_collection_by_isbn


@pytest.mark.asyncio
async def test_isbn_import_preserves_existing_holdings(
    session, test_unhydrated_editions, test_user_account
):
    collection = Collection(name="MCP import regression", user_id=test_user_account.id)
    session.add(collection)
    session.flush()
    existing_isbn, new_isbn = [edition.isbn for edition in test_unhydrated_editions[:2]]
    session.add(
        CollectionItem(
            collection_id=collection.id,
            edition_isbn=existing_isbn,
            copies_total=5,
            copies_available=0,
            info={"shelf": "A"},
        )
    )
    session.commit()
    try:
        inputs = [
            CollectionItemCreateIn(edition_isbn=isbn)
            for isbn in (existing_isbn, new_isbn)
        ]
        result = await add_editions_to_collection_by_isbn(
            session,
            inputs,
            collection,
            test_user_account,
            preserve_existing=True,
        )
        assert result == {"added": 1, "existing": 1, "valid_unique": 2}
        session.expire_all()
        holding = session.execute(
            select(CollectionItem).where(
                CollectionItem.collection_id == collection.id,
                CollectionItem.edition_isbn == existing_isbn,
            )
        ).scalar_one()
        assert (holding.copies_total, holding.copies_available, holding.info) == (
            5,
            0,
            {"shelf": "A"},
        )
        repeated = await add_editions_to_collection_by_isbn(
            session,
            inputs,
            collection,
            test_user_account,
            preserve_existing=True,
        )
        assert repeated["added"] == 0
        assert repeated["existing"] == 2
    finally:
        session.delete(collection)
        session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime_role", [False, True])
async def test_school_selection_survives_independent_storage_instances(
    settings, runtime_role, session
):
    from app.mcp.storage import build_oauth_client_storage

    if runtime_role:
        # The Docker migration runner does not apply pgroles.yaml.
        session.execute(text("GRANT USAGE ON SCHEMA public TO cloudrun"))
        session.execute(
            text(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON mcp_oauth_proxy_kv TO cloudrun"
            )
        )
        session.commit()
        settings = settings.model_copy(
            update={"POSTGRESQL_USER": "cloudrun", "POSTGRESQL_PASSWORD": "cloudrun"}
        )
    first_instance = build_oauth_client_storage(settings)
    second_instance = build_oauth_client_storage(settings)
    grant_id = str(uuid.uuid4())
    async with first_instance.key_value, second_instance.key_value:
        try:
            await first_instance.put(
                grant_id,
                {"school": "school-B"},
                collection="mcp-school-selection",
                ttl=60,
            )
            assert await second_instance.get(
                grant_id, collection="mcp-school-selection"
            ) == {"school": "school-B"}
        finally:
            await second_instance.delete(grant_id, collection="mcp-school-selection")


@pytest.mark.asyncio
async def test_mcp_school_scope_and_import_through_client(
    monkeypatch,
    test_schooladmin_account,
    test_school,
    test_unhydrated_editions,
    small_works_list,
):
    from types import SimpleNamespace

    from fastmcp import Client
    from key_value.aio.stores.memory import MemoryStore

    from app.mcp import context, server

    school_id = str(test_school.wriveted_identifier)
    claims = {
        "uid": str(test_schooladmin_account.id),
        "school_id": school_id,
        "scope": "catalogue:read books:import",
        "grant_id": str(uuid.uuid4()),
    }
    monkeypatch.setattr(
        context, "get_access_token", lambda: SimpleNamespace(claims=claims)
    )
    storage = MemoryStore()
    monkeypatch.setattr(context, "get_mcp_storage", lambda: storage)
    async with Client(server.mcp) as client:
        denied = await client.call_tool(
            "use_school", {"school": str(uuid.uuid4())}, raise_on_error=False
        )
        assert denied.is_error
        assert "not authorised" in str(denied.content)
        imported = await client.call_tool(
            "import_books",
            {"isbns": [test_unhydrated_editions[0].isbn], "school": school_id},
        )
        assert imported.data["added"] == 1
        assert imported.data["school"] == school_id
        repeated = await client.call_tool(
            "import_books", {"isbns": [test_unhydrated_editions[0].isbn]}
        )
        assert repeated.data["added"] == 0
        assert repeated.data["existing"] == 1
        found = await client.call_tool(
            "search_books", {"query": small_works_list[0].editions[0].isbn}
        )
        assert len(found.data) == 1
        assert found.data[0]["id"] == str(small_works_list[0].id)
        claims["scope"] += " books:label"
        monkeypatch.setattr(server, "enqueue_debounced_mv_refresh", lambda: None)
        labelled = await client.call_tool(
            "label_book",
            {
                "work_id": small_works_list[0].id,
                "primary_hue": "hue01_dark_suspense",
                "min_age": 5,
                "max_age": 8,
                "reading_ability": "SPOT",
                "summary": "A hopeful story for young readers.",
            },
        )
        assert labelled.data["primary_hue_key"] == "hue01_dark_suspense"
        assert labelled.data["labelled_by_user_id"] == str(test_schooladmin_account.id)
        assert labelled.data["reading_ability_keys"] == ["SPOT"]
        available = await client.call_tool("list_label_vocabulary", {})
        assert "hue01_dark_suspense" in available.data["hues"]
        assert "hue04_joyful_charming" not in available.data["hues"]
        rejected = await client.call_tool(
            "label_book",
            {
                "work_id": small_works_list[0].id,
                "primary_hue": "hue04_joyful_charming",
                "min_age": 5,
                "max_age": 8,
                "reading_ability": "SPOT",
            },
            raise_on_error=False,
        )
        assert rejected.is_error
        details = await client.call_tool(
            "get_book", {"work_id": small_works_list[0].id}
        )
        assert details.data["labelset"]["hues"][0]["key"] == "hue01_dark_suspense"
        claims["scope"] = "catalogue:read"
        denied = await client.call_tool(
            "import_books",
            {"isbns": [test_unhydrated_editions[0].isbn]},
            raise_on_error=False,
        )
        assert denied.is_error
        assert "books:import" in str(denied.content)
