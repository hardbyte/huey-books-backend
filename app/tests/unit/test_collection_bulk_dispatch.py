from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.schemas.collection import CollectionAndItemsUpdateIn, CollectionItemUpdate
from app.services import collections


@pytest.mark.parametrize("mixed_changes", [False, True])
async def test_bulk_additions_skip_individual_work_without_reordering_mixed_changes(
    monkeypatch, mixed_changes
):
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
    items = [added, updated, removed, updated_by_id] if mixed_changes else [added]
    changes = CollectionAndItemsUpdateIn(items=items)

    await collections.update_collection(session, collection, None, changes)

    add_bulk.assert_awaited_once_with(session, [added], collection, None)
    if mixed_changes:
        update_bulk.assert_awaited_once_with(
            session, [updated], collection, commit=False
        )
        assert remaining_items == items
    else:
        update_bulk.assert_not_awaited()
        assert remaining_items == []
