import asyncio
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select

from app.models import (
    Collection,
    CollectionItem,
    Edition,
    Educator,
    School,
    SchoolState,
)
from app.models.event import Event
from app.models.event_outbox import EventOutbox
from app.models.idempotency import IdempotencyRecord
from app.models.library_chat import LibraryChatSettings
from app.models.organisation import (
    LibraryMembership,
    Organisation,
    OrganisationSubscription,
)
from app.models.school import SchoolKind
from app.models.school_admin import SchoolAdmin
from app.models.subscription import Subscription, SubscriptionType
from app.models.user import User
from app.services.editions import generate_random_valid_isbn13
from app.services.security import create_access_token


def headers(user_id):
    return {
        "Authorization": "Bearer "
        + create_access_token(
            subject=f"wriveted:user-account:{user_id}",
            expires_delta=timedelta(minutes=5),
        )
    }


class Fixture:
    """The school an organisation is set up from, and its people."""

    def __init__(self, school, admin, teacher, collection, subscription, edition):
        self.school = school
        self.organisation_name = f"{school.name} group"
        self.admin = admin
        self.teacher = teacher
        self.collection = collection
        self.subscription = subscription
        self.edition = edition
        self.path = f"/v1/libraries/{school.school_uuid}/setup"

    def payload(self, **changes):
        return {
            "request_id": str(uuid4()),
            "expected_library_name": self.school.name,
            "organisation_name": self.organisation_name,
            "organisation_kind": "school",
            "new_library_name": "Senior library",
            "manager_ids": [str(self.admin.id)],
            **changes,
        }


@pytest.fixture
def setup_school(session, test_school, test_product):
    prefix = f"setup-{uuid4()}"
    school = School(
        name=prefix,
        country_code=test_school.country_code,
        state=SchoolState.ACTIVE,
        info={"location": {"state": "Test"}},
    )
    session.add(school)
    session.flush()
    admin = SchoolAdmin(
        name="Zara administrator",
        email=f"admin-{prefix}@example.com",
        school_id=school.id,
        is_active=True,
    )
    teacher = Educator(
        name="Yves teacher",
        email=f"teacher-{prefix}@example.com",
        school_id=school.id,
        is_active=True,
    )
    collection = Collection(
        name="Existing collection", school_id=school.school_uuid, is_default=True
    )
    subscription = Subscription(
        id=f"sub_{prefix}",
        school_id=school.school_uuid,
        type=SubscriptionType.SCHOOL,
        stripe_customer_id="cus_setup",
        is_active=True,
        paid_at=datetime.utcnow(),
        expiration=datetime.utcnow() + timedelta(days=30),
        product_id=test_product.id,
    )
    session.add_all([admin, teacher, collection, subscription])
    session.flush()
    edition = Edition(isbn=generate_random_valid_isbn13(), title="Existing book")
    session.add(edition)
    session.flush()
    session.add(
        CollectionItem(
            collection_id=collection.id,
            edition_isbn=edition.isbn,
            copies_total=3,
            copies_available=2,
        )
    )
    session.add(
        LibraryChatSettings(
            library_uuid=school.school_uuid,
            enabled=True,
            catalogue_policy="prefer_library",
            jokes_enabled=True,
            spelling_enabled=False,
            revision=4,
        )
    )
    session.commit()

    yield Fixture(school, admin, teacher, collection, subscription, edition)

    session.rollback()
    session.expire_all()
    _remove(session, prefix, school.school_uuid, edition.isbn)


def _remove(session, prefix, school_uuid, isbn):
    """Delete everything the test could have created, innermost first.

    Integration tests share a database and these requests commit, so the rows
    are named after the fixture prefix and removed by it.
    """
    organisation_ids = list(
        session.scalars(
            select(Organisation.id).where(Organisation.name.like(f"{prefix}%"))
        )
    )
    library_uuids = [school_uuid] + list(
        session.scalars(
            select(School.school_uuid).where(
                School.organisation_id.in_(organisation_ids)
            )
        )
        if organisation_ids
        else []
    )
    school_ids = list(
        session.scalars(select(School.id).where(School.school_uuid.in_(library_uuids)))
    )
    user_ids = list(
        session.scalars(select(User.id).where(User.email.like(f"%{prefix}%")))
    )
    session.execute(
        delete(IdempotencyRecord).where(IdempotencyRecord.actor_id.in_(user_ids))
    )
    session.execute(delete(Event).where(Event.school_id.in_(school_ids)))
    session.execute(delete(Event).where(Event.user_id.in_(user_ids)))
    session.execute(delete(EventOutbox).where(EventOutbox.user_id.in_(user_ids)))
    session.flush()
    for user_id in user_ids:
        session.delete(session.get(User, user_id))
    session.flush()
    session.execute(delete(School).where(School.school_uuid.in_(library_uuids)))
    session.flush()
    for organisation_id in organisation_ids:
        organisation = session.get(Organisation, organisation_id)
        if organisation is not None:
            session.delete(organisation)
    session.execute(delete(Edition).where(Edition.isbn == isbn))
    session.commit()


async def test_setup_creates_a_library_and_leaves_the_school_untouched(
    async_client, session, setup_school
):
    fixture = setup_school
    school_id, original_name = fixture.school.id, fixture.school.name
    collection_id = fixture.collection.id

    preview = await async_client.get(fixture.path, headers=headers(fixture.admin.id))
    assert preview.status_code == 200, preview.text
    assert preview.json()["eligible"] is True
    assert preview.json()["ineligibility"] is None
    assert preview.json()["required_manager_id"] == str(fixture.admin.id)
    # The school administrator and the home teacher; both already have access
    # to this library that the administrator could widen through People.
    assert preview.json()["eligible_manager_count"] == 2

    response = await async_client.post(
        fixture.path, headers=headers(fixture.admin.id), json=fixture.payload()
    )
    assert response.status_code == 201, response.text
    result = response.json()
    session.expire_all()

    source = session.get(School, school_id)
    assert (source.name, source.kind) == (original_name, SchoolKind.SCHOOL)
    assert source.organisation_id == UUID(result["organisation_uuid"])
    assert session.get(Collection, collection_id).school_id == source.school_uuid
    assert session.get(Educator, fixture.teacher.id).school_id == school_id
    holding = session.scalar(
        select(CollectionItem).where(CollectionItem.collection_id == collection_id)
    )
    assert (holding.copies_total, holding.copies_available) == (3, 2)
    settings = session.get(LibraryChatSettings, source.school_uuid)
    assert (settings.enabled, settings.catalogue_policy, settings.revision) == (
        True,
        "prefer_library",
        4,
    )
    assert session.get(
        OrganisationSubscription, fixture.subscription.id
    ).organisation_id
    assert session.get(Subscription, fixture.subscription.id).stripe_customer_id == (
        "cus_setup"
    )


async def test_the_new_library_is_a_library_not_an_education_unit(
    async_client, session, setup_school
):
    fixture = setup_school
    response = await async_client.post(
        fixture.path, headers=headers(fixture.admin.id), json=fixture.payload()
    )
    assert response.status_code == 201, response.text
    new_uuid = UUID(response.json()["new_library_uuid"])
    session.expire_all()

    new_library = session.scalar(select(School).where(School.school_uuid == new_uuid))
    assert new_library.kind == SchoolKind.LIBRARY
    assert new_library.name == "Senior library"
    assert new_library.state == SchoolState.INACTIVE
    assert new_library.country_code == fixture.school.country_code
    assert new_library.official_identifier is None
    assert new_library.student_domain is None and new_library.teacher_domain is None
    assert new_library.organisation_id == fixture.school.organisation_id

    collection = session.scalar(
        select(Collection).where(Collection.school_id == new_uuid)
    )
    assert (collection.name, collection.is_default, collection.book_count) == (
        "Main collection",
        True,
        0,
    )
    # Bookbot settings are left to the existing chat-settings endpoint, so the
    # library starts from the defaults for an inactive row.
    assert session.get(LibraryChatSettings, new_uuid) is None
    chat = await async_client.get(
        f"/v1/libraries/{new_uuid}/chat-settings", headers=headers(fixture.admin.id)
    )
    assert chat.status_code == 200, chat.text
    assert chat.json()["enabled"] is False
    assert chat.json()["catalogue_policy"] == "library_only"
    # Reader access comes from the organisation entitlement, not a subscription
    # of the new library's own.
    assert chat.json()["unavailable_reason"] == "disabled"


async def test_a_new_librarian_reaches_only_the_new_library(
    async_client, session, setup_school
):
    fixture = setup_school
    data = fixture.payload(
        librarian={
            "name": "New librarian",
            "email": f"librarian-{fixture.school.name}@example.com",
        }
    )
    response = await async_client.post(
        fixture.path, headers=headers(fixture.admin.id), json=data
    )
    assert response.status_code == 201, response.text
    new_uuid = UUID(response.json()["new_library_uuid"])
    session.expire_all()

    librarian = session.scalar(
        select(User).where(User.email == data["librarian"]["email"])
    )
    new_library = session.scalar(select(School).where(School.school_uuid == new_uuid))
    assert session.get(Educator, librarian.id).school_id is None
    assert session.get(LibraryMembership, (new_library.id, librarian.id)).role == (
        "manager"
    )
    assert (
        await async_client.get(
            f"/v1/libraries/{new_uuid}", headers=headers(librarian.id)
        )
    ).status_code == 200
    assert (
        await async_client.get(
            f"/v1/libraries/{fixture.school.school_uuid}", headers=headers(librarian.id)
        )
    ).status_code == 404
    # An existing teacher gains nothing at the new library.
    assert (
        await async_client.get(
            f"/v1/libraries/{new_uuid}", headers=headers(fixture.teacher.id)
        )
    ).status_code == 404
    assert (
        session.scalar(select(EventOutbox).where(EventOutbox.user_id == librarian.id))
        is not None
    )


async def test_a_retried_request_replays_its_first_result(
    async_client, session, setup_school
):
    fixture = setup_school
    data = fixture.payload()

    first = await async_client.post(
        fixture.path, headers=headers(fixture.admin.id), json=data
    )
    assert first.status_code == 201, first.text
    retried = await async_client.post(
        fixture.path, headers=headers(fixture.admin.id), json=data
    )
    assert retried.status_code == 201
    assert retried.json() == first.json()
    session.expire_all()
    assert (
        len(
            list(session.scalars(select(School).where(School.name == "Senior library")))
        )
        == 1
    )

    changed = await async_client.post(
        fixture.path,
        headers=headers(fixture.admin.id),
        json={**data, "new_library_name": "Different"},
    )
    assert changed.status_code == 409
    assert changed.json()["detail"]["code"] == "idempotency_key_reused"


async def test_a_second_request_cannot_promote_an_already_grouped_library(
    async_client, setup_school
):
    fixture = setup_school
    first = await async_client.post(
        fixture.path, headers=headers(fixture.admin.id), json=fixture.payload()
    )
    assert first.status_code == 201, first.text

    again = await async_client.post(
        fixture.path, headers=headers(fixture.admin.id), json=fixture.payload()
    )
    assert again.status_code == 409
    assert again.json()["detail"]["code"] == "already_grouped"


async def test_concurrent_submissions_produce_one_organisation(
    async_client, session, setup_school
):
    fixture = setup_school
    data = fixture.payload()

    async def submit(body):
        return await async_client.post(
            fixture.path, headers=headers(fixture.admin.id), json=body
        )

    same = await asyncio.gather(submit(data), submit(data))
    assert [response.status_code for response in same] == [201, 201], [
        response.text for response in same
    ]
    assert same[0].json() == same[1].json()
    session.expire_all()
    assert (
        len(
            list(session.scalars(select(School).where(School.name == "Senior library")))
        )
        == 1
    )


async def test_two_different_requests_cannot_both_promote(async_client, setup_school):
    fixture = setup_school

    async def submit():
        return await async_client.post(
            fixture.path, headers=headers(fixture.admin.id), json=fixture.payload()
        )

    responses = await asyncio.gather(submit(), submit())
    assert sorted(response.status_code for response in responses) == [201, 409]


@pytest.mark.parametrize(
    "case,code",
    [
        ("unpaid", "no_paid_subscription"),
        ("expired", "no_paid_subscription"),
        ("family", "no_paid_subscription"),
        ("ambiguous", "multiple_paid_subscriptions"),
        ("owned", "subscription_already_owned"),
    ],
)
async def test_payment_guards_leave_no_partial_setup(
    async_client, session, setup_school, case, code
):
    fixture = setup_school
    paid = fixture.subscription
    extra_organisation = None
    if case == "unpaid":
        paid.paid_at = None
    if case == "expired":
        paid.expiration = datetime.utcnow() - timedelta(days=1)
    if case == "family":
        paid.type = SubscriptionType.FAMILY
    if case == "ambiguous":
        session.add(
            Subscription(
                id=f"{paid.id}-extra",
                school_id=fixture.school.school_uuid,
                type=paid.type,
                stripe_customer_id=paid.stripe_customer_id,
                is_active=True,
                paid_at=paid.paid_at,
                expiration=paid.expiration,
                product_id=paid.product_id,
            )
        )
    if case == "owned":
        extra_organisation = Organisation(
            name=f"{fixture.school.name} other", kind="school"
        )
        session.add(extra_organisation)
        session.flush()
        session.add(
            OrganisationSubscription(
                subscription_id=paid.id, organisation_id=extra_organisation.id
            )
        )
    session.commit()

    preview = await async_client.get(fixture.path, headers=headers(fixture.admin.id))
    assert preview.json()["eligible"] is False
    assert preview.json()["ineligibility"]["code"] == code

    response = await async_client.post(
        fixture.path, headers=headers(fixture.admin.id), json=fixture.payload()
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == code
    session.expire_all()
    assert session.get(School, fixture.school.id).organisation_id is None
    assert session.scalar(select(IdempotencyRecord)) is None


async def test_only_home_administrators_and_staff_can_set_up(
    async_client,
    session,
    setup_school,
    test_schooladmin_account,
    test_schooladmin_account_headers,
):
    fixture = setup_school
    # A library-manager grant is catalogue authority, not payment authority.
    session.add(
        LibraryMembership(
            school_id=fixture.school.id,
            user_id=test_schooladmin_account.id,
            role="manager",
        )
    )
    session.commit()

    denied = await async_client.post(
        fixture.path,
        headers=test_schooladmin_account_headers,
        json=fixture.payload(),
    )
    assert denied.status_code == 403
    assert (
        await async_client.get(fixture.path, headers=headers(fixture.teacher.id))
    ).status_code == 403


async def test_platform_staff_can_set_up_but_not_through_view_as(
    async_client, setup_school, test_wrivetedadmin_account_headers
):
    fixture = setup_school
    staff = test_wrivetedadmin_account_headers
    impersonation = await async_client.post(
        f"/v1/auth/view-as/{fixture.admin.id}", headers=staff
    )
    assert impersonation.status_code == 200, impersonation.text

    denied = await async_client.post(
        fixture.path,
        headers={**staff, "X-View-As": impersonation.json()["context"]},
        json=fixture.payload(),
    )
    assert denied.status_code == 403, denied.text

    # Staff need not add themselves; the school keeps its own managers.
    allowed = await async_client.post(
        fixture.path, headers=staff, json=fixture.payload()
    )
    assert allowed.status_code == 201, allowed.text


async def test_stale_reviews_and_unavailable_colleagues_are_rejected(
    async_client, setup_school
):
    fixture = setup_school
    stale = await async_client.post(
        fixture.path,
        headers=headers(fixture.admin.id),
        json=fixture.payload(expected_library_name="Old name"),
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "stale_review"

    without_self = await async_client.post(
        fixture.path,
        headers=headers(fixture.admin.id),
        json=fixture.payload(manager_ids=[str(fixture.teacher.id)]),
    )
    assert without_self.status_code == 403

    unknown = await async_client.post(
        fixture.path,
        headers=headers(fixture.admin.id),
        json=fixture.payload(manager_ids=[str(fixture.admin.id), str(uuid4())]),
    )
    assert unknown.status_code == 409
    assert unknown.json()["detail"]["code"] == "manager_unavailable"


async def test_only_people_who_already_manage_the_library_may_manage_the_group(
    async_client, session, setup_school
):
    fixture = setup_school
    reviewer = Educator(
        name="Quinn reviewer",
        email=f"reviewer-{fixture.school.name}@example.com",
        school_id=None,
        is_active=True,
    )
    session.add(reviewer)
    session.flush()
    session.add(
        LibraryMembership(
            school_id=fixture.school.id, user_id=reviewer.id, role="reviewer"
        )
    )
    session.commit()

    listed = await async_client.get(
        f"{fixture.path}/managers", headers=headers(fixture.admin.id)
    )
    assert str(reviewer.id) not in {row["user_id"] for row in listed.json()["data"]}

    rejected = await async_client.post(
        fixture.path,
        headers=headers(fixture.admin.id),
        json=fixture.payload(manager_ids=[str(fixture.admin.id), str(reviewer.id)]),
    )
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "manager_unavailable"


async def test_manager_choices_are_scoped_searchable_and_paged(
    async_client, session, setup_school
):
    fixture = setup_school
    for index in range(30):
        session.add(
            Educator(
                name=f"Colleague {index:02d}",
                email=f"member-{index}-{fixture.school.name}@example.com",
                school_id=fixture.school.id,
                is_active=True,
            )
        )
    session.commit()

    preview = await async_client.get(fixture.path, headers=headers(fixture.admin.id))
    assert preview.json()["eligible_manager_count"] == 32

    first = await async_client.get(
        f"{fixture.path}/managers", headers=headers(fixture.admin.id)
    )
    assert (first.json()["total"], first.json()["skip"], first.json()["limit"]) == (
        32,
        0,
        25,
    )
    assert len(first.json()["data"]) == 25

    last = await async_client.get(
        f"{fixture.path}/managers?skip=25", headers=headers(fixture.admin.id)
    )
    assert len(last.json()["data"]) == 7

    searched = await async_client.get(
        f"{fixture.path}/managers?q=administrator", headers=headers(fixture.admin.id)
    )
    assert searched.json()["total"] == 1
    assert searched.json()["data"][0]["user_id"] == str(fixture.admin.id)

    # A wildcard in the query is matched literally, not expanded.
    assert (
        await async_client.get(
            f"{fixture.path}/managers?q=%25", headers=headers(fixture.admin.id)
        )
    ).json()["total"] == 0


async def test_a_failure_rolls_back_the_group_and_the_new_person(
    async_client, session, setup_school, monkeypatch
):
    from app.services import people

    fixture = setup_school

    async def failing_notify(*args, **kwargs):
        raise RuntimeError("Synthetic outbox failure")

    monkeypatch.setattr(people, "notify", failing_notify)
    data = fixture.payload(
        librarian={
            "name": "Never created",
            "email": f"rollback-{fixture.school.name}@example.com",
        }
    )
    with pytest.raises(RuntimeError, match="Synthetic"):
        await async_client.post(
            fixture.path, headers=headers(fixture.admin.id), json=data
        )

    session.expire_all()
    assert session.get(School, fixture.school.id).organisation_id is None
    assert (
        session.scalar(select(User.id).where(User.email == data["librarian"]["email"]))
        is None
    )
    assert session.scalar(select(IdempotencyRecord)) is None
    assert session.scalar(select(School).where(School.name == "Senior library")) is None


async def test_home_roles_are_not_offered_at_the_new_library(
    async_client, session, setup_school
):
    """Education roles belong to an education unit, so the new library has none."""
    fixture = setup_school
    response = await async_client.post(
        fixture.path, headers=headers(fixture.admin.id), json=fixture.payload()
    )
    assert response.status_code == 201, response.text
    new_uuid = response.json()["new_library_uuid"]

    people = await async_client.get(
        f"/v1/libraries/{new_uuid}/people", headers=headers(fixture.admin.id)
    )
    assert people.status_code == 200, people.text
    assert set(people.json()["available_roles"]) == {
        "manager",
        "reviewer",
        "cataloguer",
    }

    rejected = await async_client.post(
        f"/v1/libraries/{new_uuid}/people",
        headers=headers(fixture.admin.id),
        json={
            "name": "Home educator",
            "email": f"home-{fixture.school.name}@example.com",
            "role": "educator",
        },
    )
    assert rejected.status_code == 403, rejected.text

    # The source school still offers them.
    source_people = await async_client.get(
        f"/v1/libraries/{fixture.school.school_uuid}/people",
        headers=headers(fixture.admin.id),
    )
    assert "school_admin" in source_people.json()["available_roles"]
