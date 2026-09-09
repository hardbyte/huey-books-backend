import asyncio

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.models import Collection, CollectionItem, Edition
from app.schemas.organisation import CollectionImport
from app.services.editions import generate_random_valid_isbn13
from app.services.organisation_management import import_collection
from app.services.workspace_errors import WorkspaceConflict


@pytest.fixture
def import_catalogue(session, test_school):
    collection = Collection(name="Count preview", school_id=test_school.school_uuid)
    sibling = Collection(name="Untouched catalogue", school_id=test_school.school_uuid)
    session.add_all([collection, sibling])
    session.commit()
    isbns = [generate_random_valid_isbn13() for _ in range(3)]
    identifiers = collection.id, sibling.id
    yield test_school.school_uuid, collection.id, sibling.id, isbns
    session.rollback()
    session.execute(delete(Collection).where(Collection.id.in_(identifiers)))
    session.execute(delete(Edition).where(Edition.isbn.in_(isbns)))
    session.commit()


async def test_preview_reports_current_and_proposed_without_writes(
    async_client, import_catalogue, session, test_wrivetedadmin_account_headers
):
    library, collection, sibling, isbns = import_catalogue
    path = f"/v1/libraries/{library}/collections/{collection}/import"
    headers = test_wrivetedadmin_account_headers
    initial = await async_client.post(
        path,
        headers=headers,
        json={
            "dry_run": False,
            "items": [
                {
                    "edition_isbn": isbns[0],
                    "title": "Existing title",
                    "copies_total": 4,
                },
                {"edition_isbn": isbns[1], "copies_total": 0},
            ],
        },
    )
    assert initial.status_code == 200, initial.text
    preview = await async_client.post(
        path,
        headers=headers,
        json={
            "items": [
                {"edition_isbn": isbns[2], "title": "New title", "copies_total": 2},
                {"edition_isbn": isbns[0], "copies_total": 3},
                {"edition_isbn": isbns[1], "copies_total": 0},
            ],
        },
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["changes"] == [
        {
            "edition_isbn": isbns[2],
            "title": "New title",
            "action": "added",
            "current_copies_total": None,
            "proposed_copies_total": 2,
        },
        {
            "edition_isbn": isbns[0],
            "title": "Existing title",
            "action": "updated",
            "current_copies_total": 4,
            "proposed_copies_total": 3,
        },
        {
            "edition_isbn": isbns[1],
            "title": None,
            "action": "unchanged",
            "current_copies_total": 0,
            "proposed_copies_total": 0,
        },
    ]
    assert preview.json()["collection_size"] == 3
    assert session.scalar(select(Edition.id).where(Edition.isbn == isbns[2])) is None
    rows = session.scalars(
        select(CollectionItem).where(CollectionItem.collection_id == collection)
    ).all()
    assert {row.edition_isbn: row.copies_total for row in rows} == {
        isbns[0]: 4,
        isbns[1]: 0,
    }
    assert (
        session.scalar(
            select(CollectionItem.id).where(CollectionItem.collection_id == sibling)
        )
        is None
    )


async def test_expected_count_conflict_rolls_back_whole_import(
    async_client, import_catalogue, session, test_wrivetedadmin_account_headers
):
    library, collection, _, isbns = import_catalogue
    path = f"/v1/libraries/{library}/collections/{collection}/import"
    headers = test_wrivetedadmin_account_headers
    created = await async_client.post(
        path,
        headers=headers,
        json={
            "dry_run": False,
            "items": [{"edition_isbn": isbns[0], "copies_total": 4}],
        },
    )
    assert created.status_code == 200, created.text
    for dry_run in (True, False):
        rejected = await async_client.post(
            path,
            headers=headers,
            json={
                "dry_run": dry_run,
                "items": [
                    {"edition_isbn": isbns[1], "copies_total": 2},
                    {
                        "edition_isbn": isbns[0],
                        "copies_total": 3,
                        "expected_copies_total": 5,
                    },
                    {
                        "edition_isbn": isbns[2],
                        "copies_total": 1,
                        "expected_copies_total": 0,
                    },
                ],
            },
        )
        assert rejected.status_code == 409, rejected.text
        assert rejected.json()["detail"]["conflicts"] == [
            {
                "edition_isbn": isbns[0],
                "expected_copies_total": 5,
                "current_copies_total": 4,
            },
            {
                "edition_isbn": isbns[2],
                "expected_copies_total": 0,
                "current_copies_total": None,
            },
        ]
    assert session.scalar(select(Edition.id).where(Edition.isbn == isbns[1])) is None
    assert session.scalar(select(Edition.id).where(Edition.isbn == isbns[2])) is None
    row = session.scalar(
        select(CollectionItem).where(CollectionItem.collection_id == collection)
    )
    assert row.copies_total == 4


async def test_competing_expected_count_edits_allow_only_one_save(
    async_client, import_catalogue, session, test_wrivetedadmin_account_headers
):
    library, collection, _, isbns = import_catalogue
    path = f"/v1/libraries/{library}/collections/{collection}/import"
    headers = test_wrivetedadmin_account_headers
    created = await async_client.post(
        path,
        headers=headers,
        json={
            "dry_run": False,
            "items": [{"edition_isbn": isbns[0], "copies_total": 4}],
        },
    )
    assert created.status_code == 200, created.text
    responses = await asyncio.gather(
        *[
            async_client.post(
                path,
                headers=headers,
                json={
                    "dry_run": False,
                    "items": [
                        {
                            "edition_isbn": isbns[0],
                            "copies_total": proposed,
                            "expected_copies_total": 4,
                        }
                    ],
                },
            )
            for proposed in (2, 3)
        ]
    )
    assert sorted(response.status_code for response in responses) == [200, 409], [
        response.text for response in responses
    ]
    saved = next(
        response.json() for response in responses if response.status_code == 200
    )
    row = session.scalar(
        select(CollectionItem).where(CollectionItem.collection_id == collection)
    )
    assert row.copies_total == saved["changes"][0]["proposed_copies_total"]


async def test_expected_zero_can_be_updated_and_preserves_checked_out_copies(
    async_client, import_catalogue, session, test_wrivetedadmin_account_headers
):
    library, collection, _, isbns = import_catalogue
    path = f"/v1/libraries/{library}/collections/{collection}/import"
    headers = test_wrivetedadmin_account_headers
    created = await async_client.post(
        path,
        headers=headers,
        json={
            "dry_run": False,
            "items": [{"edition_isbn": isbns[0], "copies_total": 0}],
        },
    )
    assert created.status_code == 200, created.text
    updated = await async_client.post(
        path,
        headers=headers,
        json={
            "dry_run": False,
            "items": [
                {
                    "edition_isbn": isbns[0],
                    "copies_total": 4,
                    "expected_copies_total": 0,
                }
            ],
        },
    )
    assert updated.status_code == 200, updated.text
    row = session.scalar(
        select(CollectionItem).where(CollectionItem.collection_id == collection)
    )
    row.copies_available = 2
    session.commit()
    updated = await async_client.post(
        path,
        headers=headers,
        json={
            "dry_run": False,
            "items": [
                {
                    "edition_isbn": isbns[0],
                    "copies_total": 3,
                    "expected_copies_total": 4,
                }
            ],
        },
    )
    assert updated.status_code == 200, updated.text
    session.refresh(row)
    assert (row.copies_total, row.copies_available) == (3, 1)


async def test_competing_new_holding_previews_require_expected_absence(
    async_client,
    import_catalogue,
    session,
    test_wrivetedadmin_account,
    test_wrivetedadmin_account_headers,
):
    library, collection, _, isbns = import_catalogue
    path = f"/v1/libraries/{library}/collections/{collection}/import"
    for _ in range(2):
        preview = await async_client.post(
            path,
            headers=test_wrivetedadmin_account_headers,
            json={"items": [{"edition_isbn": isbns[0], "copies_total": 2}]},
        )
        assert preview.status_code == 200, preview.text
        assert preview.json()["changes"][0]["current_copies_total"] is None
    engine = create_async_engine(get_settings().SQLALCHEMY_ASYNC_URI)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def save(proposed_count):
        async with sessions() as connection:
            try:
                await import_collection(
                    library,
                    collection,
                    CollectionImport(
                        dry_run=False,
                        items=[
                            {
                                "edition_isbn": isbns[0],
                                "copies_total": proposed_count,
                                "expected_copies_total": None,
                            }
                        ],
                    ),
                    connection,
                    test_wrivetedadmin_account,
                )
                return 200
            except WorkspaceConflict as error:
                assert error.detail["code"] == "collection_count_conflict"
                assert error.detail["conflicts"][0]["expected_copies_total"] is None
                assert error.detail["conflicts"][0]["current_copies_total"] in (2, 3)
                return 409

    try:
        assert sorted(await asyncio.gather(save(2), save(3))) == [200, 409]
    finally:
        await engine.dispose()
    row = session.scalar(
        select(CollectionItem).where(CollectionItem.collection_id == collection)
    )
    assert row.copies_total in (2, 3)
