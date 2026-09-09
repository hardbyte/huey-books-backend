from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.collection import Collection
from app.models.collection_item import CollectionItem
from app.models.edition import Edition
from app.repositories.collection_repository import collection_repository
from app.repositories.school_repository import school_repository
from app.schemas.collection import CollectionCreateIn, CollectionItemCreateIn
from app.services.collection_errors import (
    CollectionOwnerChangeError,
    DefaultCollectionInUseError,
)
from app.services.collection_service import CollectionService
from app.services.editions import generate_random_valid_isbn13


def test_default_collection_lookup_preserves_additional_collection(
    session, test_school
):
    default = collection_repository.create(
        session,
        CollectionCreateIn(name="Default", school_id=test_school.school_uuid),
    )
    additional = Collection(name="Additional", school_id=test_school.school_uuid)
    session.add(additional)
    session.commit()
    session.expire(test_school)

    assert test_school.collection.id == default.id
    assert {collection.id for collection in test_school.collections} == {
        default.id,
        additional.id,
    }
    found, created = collection_repository.get_or_create(
        session,
        CollectionCreateIn(name="Legacy import", school_id=test_school.school_uuid),
    )
    assert found.id == default.id
    assert not created
    assert additional.name == "Additional"
    assert not additional.is_default


def test_replacing_collection_preserves_identity_and_other_holdings(
    session, test_school, test_unhydrated_editions
):
    isbn = test_unhydrated_editions[0].isbn
    default = collection_repository.create(
        session,
        CollectionCreateIn(
            name="Default",
            school_id=test_school.school_uuid,
            items=[CollectionItemCreateIn(edition_isbn=isbn, copies_total=7)],
        ),
    )
    additional = Collection(name="Additional", school_id=test_school.school_uuid)
    session.add(additional)
    session.flush()
    additional_id = additional.id
    result = CollectionService().replace_collection(
        session,
        existing=additional,
        data=CollectionCreateIn(
            name="New inventory",
            school_id=test_school.school_uuid,
            items=[CollectionItemCreateIn(edition_isbn=isbn, copies_total=2)],
        ),
        ignore_conflicts=False,
    )
    assert result.id == additional_id
    assert not result.is_default
    assert (
        session.scalar(
            select(CollectionItem.copies_total).where(
                CollectionItem.collection_id == default.id,
                CollectionItem.edition_isbn == isbn,
            )
        )
        == 7
    )
    assert (
        session.scalar(
            select(CollectionItem.copies_total).where(
                CollectionItem.collection_id == additional_id,
                CollectionItem.edition_isbn == isbn,
            )
        )
        == 2
    )
    replaced_default = CollectionService().replace_collection(
        session,
        existing=default,
        data=CollectionCreateIn(
            name="Default retained", school_id=test_school.school_uuid
        ),
        ignore_conflicts=False,
    )
    assert replaced_default.id == default.id
    assert replaced_default.is_default
    assert (
        session.scalar(
            select(CollectionItem.copies_total).where(
                CollectionItem.collection_id == additional_id,
            )
        )
        == 2
    )


def test_collection_replacement_cannot_change_owner(session, test_school):
    default = collection_repository.create(
        session,
        CollectionCreateIn(name="Default", school_id=test_school.school_uuid),
    )
    with pytest.raises(CollectionOwnerChangeError, match="owner cannot be changed"):
        CollectionService().replace_collection(
            session,
            existing=default,
            data=CollectionCreateIn(name="Different owner", school_id=uuid4()),
            ignore_conflicts=False,
        )
    assert default.name == "Default"


def test_failed_replacement_rolls_back_original_holdings_and_new_edition(
    session, test_school, test_unhydrated_editions
):
    original_isbn = test_unhydrated_editions[0].isbn
    collection = collection_repository.create(
        session,
        CollectionCreateIn(
            name="Original inventory",
            school_id=test_school.school_uuid,
            items=[CollectionItemCreateIn(edition_isbn=original_isbn, copies_total=7)],
        ),
    )
    collection_id = collection.id
    new_isbn = generate_random_valid_isbn13()
    assert session.scalar(select(Edition).where(Edition.isbn == new_isbn)) is None
    with pytest.raises(IntegrityError):
        CollectionService().replace_collection(
            session,
            existing=collection,
            data=CollectionCreateIn(
                name="Failed inventory",
                school_id=test_school.school_uuid,
                items=[CollectionItemCreateIn(edition_isbn=new_isbn)] * 2,
            ),
            ignore_conflicts=False,
        )
    session.rollback()
    saved = session.get(Collection, collection_id)
    assert saved.name == "Original inventory"
    assert saved.is_default
    holdings = session.execute(
        select(CollectionItem.edition_isbn, CollectionItem.copies_total).where(
            CollectionItem.collection_id == collection_id
        )
    ).all()
    assert holdings == [(original_isbn, 7)]
    assert session.scalar(select(Edition).where(Edition.isbn == new_isbn)) is None


def test_default_collection_cannot_be_deleted_with_additional_collections(
    session, test_school
):
    default = collection_repository.create(
        session,
        CollectionCreateIn(name="Default", school_id=test_school.school_uuid),
    )
    session.add(Collection(name="Additional", school_id=test_school.school_uuid))
    session.commit()
    with pytest.raises(DefaultCollectionInUseError, match="additional collections"):
        CollectionService().delete_collection(session, collection=default)


def test_connected_school_filter_does_not_duplicate_multiple_collections(
    session, test_school, test_unhydrated_editions
):
    for is_default in (True, False):
        collection = Collection(
            name=f"Inventory {is_default}",
            school_id=test_school.school_uuid,
            is_default=is_default,
        )
        session.add(collection)
        session.flush()
        session.add(
            CollectionItem(
                collection_id=collection.id,
                edition_isbn=test_unhydrated_editions[0].isbn,
            )
        )
    session.commit()
    connected = school_repository.get_all_query_with_optional_filters(
        session,
        official_identifier=test_school.official_identifier,
        is_collection_connected=True,
    )
    assert [school.id for school in session.scalars(connected)] == [test_school.id]
    disconnected = school_repository.get_all_query_with_optional_filters(
        session,
        official_identifier=test_school.official_identifier,
        is_collection_connected=False,
    )
    assert list(session.scalars(disconnected)) == []


def test_collection_owner_error_remains_http_422(
    client, session, test_school, backend_service_account_headers
):
    default = collection_repository.create(
        session, CollectionCreateIn(name="Default", school_id=test_school.school_uuid)
    )
    response = client.put(
        f"/v1/collection/{default.id}",
        headers=backend_service_account_headers,
        json={"name": "Changed", "school_id": str(uuid4())},
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "A collection's owner cannot be changed."
    session.refresh(default)
    assert default.name == "Default"
    assert default.school_id == test_school.school_uuid


def test_default_collection_error_remains_http_409(
    client, session, test_school, backend_service_account_headers
):
    default = collection_repository.create(
        session, CollectionCreateIn(name="Default", school_id=test_school.school_uuid)
    )
    additional = Collection(name="Additional", school_id=test_school.school_uuid)
    session.add(additional)
    session.commit()
    default_id, additional_id = default.id, additional.id
    response = client.delete(
        f"/v1/collection/{default_id}", headers=backend_service_account_headers
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == (
        "Remove the additional collections before deleting the default collection."
    )
    for identifier in (additional_id, default_id):
        response = client.delete(
            f"/v1/collection/{identifier}", headers=backend_service_account_headers
        )
        assert response.status_code == 200, response.text
    session.expire_all()
    assert session.get(Collection, default_id) is None
    assert session.get(Collection, additional_id) is None


def test_repository_collection_delete_does_not_commit(session, test_school):
    default = collection_repository.create(
        session, CollectionCreateIn(name="Default", school_id=test_school.school_uuid)
    )
    identifier = default.id
    with session.begin_nested() as savepoint:
        collection_repository.lock_library(session, test_school.school_uuid)
        assert not collection_repository.has_other_collections(session, default)
        collection_repository.delete_by_id(session, identifier)
        assert session.scalar(select(Collection.id).where(Collection.id == identifier)) is None
        savepoint.rollback()
    assert session.scalar(select(Collection.id).where(Collection.id == identifier)) == identifier
