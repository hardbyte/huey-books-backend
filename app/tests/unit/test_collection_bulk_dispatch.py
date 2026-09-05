from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.schemas.collection import CollectionAndItemsUpdateIn, CollectionItemUpdate
from app.services import collections


async def test_bulk_isbn_changes_are_not_processed_again_individually(monkeypatch):
    session = MagicMock()
    collection = SimpleNamespace(id="test", name="Test catalogue")
    add_bulk = AsyncMock()
    update_bulk = AsyncMock()
    remaining_items = []

    def update_remaining(*args, **kwargs):
        remaining_items.extend(kwargs["obj_in"].items)
        return collection

    monkeypatch.setattr(collections, "add_editions_to_collection_by_isbn", add_bulk)
    monkeypatch.setattr(
        collections, "bulk_update_editions_in_collection_by_isbn", update_bulk
    )
    monkeypatch.setattr(collections.crud.collection, "update", update_remaining)
    added = CollectionItemUpdate(edition_isbn="9780394820378", action="add")
    updated = CollectionItemUpdate(edition_isbn="9780140328721", action="update")
    removed = CollectionItemUpdate(edition_isbn="9780064400558", action="remove")
    updated_by_id = CollectionItemUpdate(id=42, action="update", copies_total=2)
    changes = CollectionAndItemsUpdateIn(items=[added, updated, removed, updated_by_id])

    await collections.update_collection(session, collection, None, changes)

    add_bulk.assert_awaited_once_with(session, [added], collection, None)
    update_bulk.assert_awaited_once_with(session, [updated], collection, commit=False)
    assert remaining_items == [removed, updated_by_id]
