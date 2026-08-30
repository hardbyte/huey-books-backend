"""Integration tests for the self-service onboarding endpoints."""

import secrets

from app.models import SchoolState
from app.models.user import UserAccountType

# ── Family onboarding ─────────────────────────────────────────────────


def test_onboard_family(client, test_user_account, test_user_account_token):
    """A public user can become a parent with child readers."""
    response = client.post(
        "/v1/onboarding/family",
        headers={"Authorization": f"Bearer {test_user_account_token}"},
        json={
            "parent_name": "Test Parent",
            "children": [
                {"name": "Alice", "age": 8, "reading_ability": "TREEHOUSE"},
                {"name": "Bob", "age": 11},
            ],
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["children_created"] == 2
    assert data["parent_id"] is not None


def test_onboard_family_promotes_to_parent(
    client, session_factory, test_user_account_token
):
    """The user's type is promoted from PUBLIC to PARENT."""
    from app.models.user import User
    from app.services.security import get_payload_from_access_token

    payload = get_payload_from_access_token(test_user_account_token)
    user_id = payload.sub.split(":")[-1]

    response = client.post(
        "/v1/onboarding/family",
        headers={"Authorization": f"Bearer {test_user_account_token}"},
        json={
            "parent_name": "Promoted Parent",
            "children": [{"name": "Charlie", "age": 6}],
        },
    )
    assert response.status_code == 200

    with session_factory() as fresh_session:
        user = fresh_session.get(User, user_id)
        assert user is not None
        assert user.type == UserAccountType.PARENT


def test_onboard_family_unauthenticated(client):
    """Unauthenticated requests are rejected."""
    response = client.post(
        "/v1/onboarding/family",
        json={
            "parent_name": "Test",
            "children": [{"name": "Kid"}],
        },
    )
    assert response.status_code in (401, 403)


# ── School onboarding ─────────────────────────────────────────────────


def test_onboard_new_school(client, test_user_account, test_user_account_token):
    """A public user can create a new school and become its admin."""
    school_name = f"Test Onboarding School {secrets.token_hex(4)}"
    response = client.post(
        "/v1/onboarding/school",
        headers={"Authorization": f"Bearer {test_user_account_token}"},
        json={
            "school_name": school_name,
            "country_code": "ATA",
            "location": {"state": "TestState", "postcode": "0000"},
            "contact_name": "Test Teacher",
            "contact_email": "teacher@test.com",
            "contact_role": "teacher",
            "student_count_estimate": 200,
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["school_name"] == school_name
    assert data["school_state"] == SchoolState.PENDING.value
    assert data["school_wriveted_id"] is not None


def test_onboard_existing_school(
    client, session, test_user_account, test_user_account_token
):
    """A public user can select an existing inactive school and request onboarding."""
    from app.models import School
    from app.services.experiments import get_experiments

    # Create a school directly
    school = School(
        name=f"Existing School {secrets.token_hex(4)}",
        country_code="ATA",
        state=SchoolState.INACTIVE,
        info={
            "location": {"state": "Test", "postcode": "1234"},
            "experiments": get_experiments({}),
        },
    )
    session.add(school)
    session.commit()
    session.refresh(school)
    wriveted_id = str(school.wriveted_identifier)

    response = client.post(
        "/v1/onboarding/school",
        headers={"Authorization": f"Bearer {test_user_account_token}"},
        json={
            "school_wriveted_id": wriveted_id,
            "contact_name": "Test Librarian",
            "contact_email": "librarian@test.com",
            "contact_role": "librarian",
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["school_wriveted_id"] == wriveted_id
    assert data["school_state"] == SchoolState.PENDING.value


def test_onboard_active_school_rejected(
    client, session, test_user_account, test_user_account_token
):
    """Cannot onboard to a school that is already active."""
    from app.models import School
    from app.services.experiments import get_experiments

    school = School(
        name=f"Active School {secrets.token_hex(4)}",
        country_code="ATA",
        state=SchoolState.ACTIVE,
        info={
            "location": {"state": "Test", "postcode": "1234"},
            "experiments": get_experiments({}),
        },
    )
    session.add(school)
    session.commit()
    session.refresh(school)

    response = client.post(
        "/v1/onboarding/school",
        headers={"Authorization": f"Bearer {test_user_account_token}"},
        json={
            "school_wriveted_id": str(school.wriveted_identifier),
            "contact_name": "Test",
            "contact_email": "test@test.com",
            "contact_role": "teacher",
        },
    )
    assert response.status_code == 409


def test_onboard_missing_school_name_rejected(
    client, test_user_account, test_user_account_token
):
    """Creating a new school requires school_name and country_code."""
    response = client.post(
        "/v1/onboarding/school",
        headers={"Authorization": f"Bearer {test_user_account_token}"},
        json={
            "contact_name": "Test",
            "contact_email": "test@test.com",
            "contact_role": "teacher",
        },
    )
    assert response.status_code == 422


def test_onboard_unauthenticated_rejected(client):
    """Unauthenticated requests are rejected."""
    response = client.post(
        "/v1/onboarding/school",
        json={
            "school_name": "Test",
            "country_code": "ATA",
            "contact_name": "Test",
            "contact_email": "test@test.com",
            "contact_role": "teacher",
        },
    )
    assert response.status_code in (401, 403)


def test_onboard_creates_event(
    client, session, test_user_account, test_user_account_token
):
    """Onboarding creates an event visible in the admin UI."""
    from app.models.event import Event

    school_name = f"Event Test School {secrets.token_hex(4)}"
    response = client.post(
        "/v1/onboarding/school",
        headers={"Authorization": f"Bearer {test_user_account_token}"},
        json={
            "school_name": school_name,
            "country_code": "ATA",
            "location": {"state": "Test", "postcode": "0000"},
            "contact_name": "Event Tester",
            "contact_email": "events@test.com",
            "contact_role": "principal",
        },
    )
    assert response.status_code == 200

    # Check an event was created
    events = (
        session.query(Event).filter(Event.title == "School onboarding request").all()
    )
    matching = [e for e in events if school_name in (e.description or "")]
    assert len(matching) >= 1
    assert matching[0].info["contact_name"] == "Event Tester"


def test_onboard_promotes_user_to_school_admin(
    client, session_factory, test_user_account_token
):
    """The user's account type is promoted from PUBLIC to SCHOOL_ADMIN."""
    from app.services.security import get_payload_from_access_token

    payload = get_payload_from_access_token(test_user_account_token)
    user_id = payload.sub.split(":")[-1]

    school_name = f"Promotion Test School {secrets.token_hex(4)}"
    response = client.post(
        "/v1/onboarding/school",
        headers={"Authorization": f"Bearer {test_user_account_token}"},
        json={
            "school_name": school_name,
            "country_code": "ATA",
            "location": {"state": "Test", "postcode": "0000"},
            "contact_name": "Promote Test",
            "contact_email": "promote@test.com",
            "contact_role": "librarian",
        },
    )
    assert response.status_code == 200

    # Query in a fresh session to see the promoted type
    with session_factory() as fresh_session:
        from app.models.user import User

        user = fresh_session.get(User, user_id)
        assert user is not None
        assert user.type == UserAccountType.SCHOOL_ADMIN


# ── School onboarding deduplication ────────────────────────────────────


def _second_user_and_token(session):
    """Create an additional public user account and return (user, token)."""
    from datetime import timedelta

    from app import crud
    from app.schemas.users.user_create import UserCreateIn
    from app.services.security import create_access_token
    from app.tests.util.random_strings import random_lower_string

    user = crud.user.create(
        db=session,
        obj_in=UserCreateIn(
            name="integration test account (second public)",
            email=f"{random_lower_string(6)}@test.com",
            first_name="Second",
            last_name_initial="U",
        ),
    )
    token = create_access_token(
        subject=f"wriveted:user-account:{user.id}",
        expires_delta=timedelta(minutes=5),
    )
    return user, token


def _count_schools(session, name, country_code):
    from sqlalchemy import func

    from app.models import School

    return (
        session.query(School)
        .filter(
            func.lower(func.btrim(School.name)) == name.strip().lower(),
            School.country_code == country_code,
        )
        .count()
    )


def _make_school(
    session, *, name, country_code, state, location=None, official_identifier=None
):
    """Create a school row directly (for dedup fixtures)."""
    from app.models import School
    from app.services.experiments import get_experiments

    school = School(
        name=name,
        country_code=country_code,
        state=state,
        official_identifier=official_identifier,
        info={"location": location or {}, "experiments": get_experiments({})},
    )
    session.add(school)
    session.commit()
    session.refresh(school)
    return school


def _onboard_payload(name, country_code="ATA", **extra):
    payload = {
        "school_name": name,
        "country_code": country_code,
        "contact_name": "Teacher",
        "contact_email": "teacher@test.com",
        "contact_role": "teacher",
    }
    payload.update(extra)
    return payload


def test_onboard_new_school_creates_single_record(
    client, session, test_user_account, test_user_account_token
):
    """A brand-new name+country creates exactly one school."""
    name = f"Dedup New School {secrets.token_hex(4)}"
    resp = client.post(
        "/v1/onboarding/school",
        headers={"Authorization": f"Bearer {test_user_account_token}"},
        json=_onboard_payload(name),
    )
    assert resp.status_code == 200, resp.text
    assert _count_schools(session, name, "ATA") == 1


def test_onboard_second_registration_without_location_is_ambiguous(
    client, session, test_user_account, test_user_account_token
):
    """A colleague re-registering the same name+country without a confirming
    signal is ambiguous — 409, and no duplicate row is created."""
    name = f"Dedup Colleague School {secrets.token_hex(4)}"
    first = client.post(
        "/v1/onboarding/school",
        headers={"Authorization": f"Bearer {test_user_account_token}"},
        json=_onboard_payload(name, contact_email="first@test.com"),
    )
    assert first.status_code == 200, first.text
    assert _count_schools(session, name, "ATA") == 1

    _second, second_token = _second_user_and_token(session)
    second = client.post(
        "/v1/onboarding/school",
        headers={"Authorization": f"Bearer {second_token}"},
        json=_onboard_payload(name, contact_email="second@test.com"),
    )
    assert second.status_code == 409, second.text
    assert _count_schools(session, name, "ATA") == 1


def test_onboard_attaches_to_inactive_when_location_matches(
    client, session, test_user_account, test_user_account_token
):
    """An inactive, admin-less school is reused when the location confirms it's
    the same one (case-insensitive name), rather than duplicated."""
    name = f"Inactive Dedup School {secrets.token_hex(4)}"
    school = _make_school(
        session,
        name=name,
        country_code="ATA",
        state=SchoolState.INACTIVE,
        location={"postcode": "7000"},
    )
    resp = client.post(
        "/v1/onboarding/school",
        headers={"Authorization": f"Bearer {test_user_account_token}"},
        json=_onboard_payload(name.upper(), location={"postcode": "7000"}),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["school_wriveted_id"] == str(school.wriveted_identifier)
    assert _count_schools(session, name, "ATA") == 1


def test_onboard_matching_active_school_rejected(
    client, session, test_user_account, test_user_account_token
):
    """A confirmed match against an ACTIVE school is rejected (not claimable)."""
    name = f"Active Dedup School {secrets.token_hex(4)}"
    _make_school(
        session,
        name=name,
        country_code="ATA",
        state=SchoolState.ACTIVE,
        location={"postcode": "7000"},
    )
    resp = client.post(
        "/v1/onboarding/school",
        headers={"Authorization": f"Bearer {test_user_account_token}"},
        json=_onboard_payload(name, location={"postcode": "7000"}),
    )
    assert resp.status_code == 409, resp.text
    assert _count_schools(session, name, "ATA") == 1


def test_onboard_ambiguous_different_location_then_create_new(
    client, session, test_user_account, test_user_account_token
):
    """Same name, DIFFERENT location is ambiguous (409); create_new_school makes
    a genuinely distinct second record."""
    name = f"SameName Diff Location {secrets.token_hex(4)}"
    _make_school(
        session,
        name=name,
        country_code="ATA",
        state=SchoolState.INACTIVE,
        location={"postcode": "1000"},
    )
    ambiguous = client.post(
        "/v1/onboarding/school",
        headers={"Authorization": f"Bearer {test_user_account_token}"},
        json=_onboard_payload(name, location={"postcode": "2000"}),
    )
    assert ambiguous.status_code == 409, ambiguous.text
    body = ambiguous.json()["detail"]
    assert "candidates" in body and len(body["candidates"]) >= 1
    assert _count_schools(session, name, "ATA") == 1

    created = client.post(
        "/v1/onboarding/school",
        headers={"Authorization": f"Bearer {test_user_account_token}"},
        json=_onboard_payload(
            name, location={"postcode": "2000"}, create_new_school=True
        ),
    )
    assert created.status_code == 200, created.text
    assert _count_schools(session, name, "ATA") == 2


def test_onboard_same_name_different_country_are_separate(
    client, session, test_user_account, test_user_account_token
):
    """Exact-country isolation: the same name in another country is a different
    school and is created independently."""
    name = f"Cross Country School {secrets.token_hex(4)}"
    _make_school(
        session,
        name=name,
        country_code="ATA",
        state=SchoolState.INACTIVE,
        location={"postcode": "1000"},
    )
    resp = client.post(
        "/v1/onboarding/school",
        headers={"Authorization": f"Bearer {test_user_account_token}"},
        json=_onboard_payload(name, country_code="NZL"),
    )
    assert resp.status_code == 200, resp.text
    assert _count_schools(session, name, "ATA") == 1
    assert _count_schools(session, name, "NZL") == 1


def test_onboard_whitespace_does_not_duplicate(
    client, session, test_user_account, test_user_account_token
):
    """A trailing-whitespace resubmit matches the trimmed stored name — it does
    not create a second record."""
    base = f"Whitespace School {secrets.token_hex(4)}"
    first = client.post(
        "/v1/onboarding/school",
        headers={"Authorization": f"Bearer {test_user_account_token}"},
        json=_onboard_payload(base + "   "),
    )
    assert first.status_code == 200, first.text
    assert _count_schools(session, base, "ATA") == 1

    _second, second_token = _second_user_and_token(session)
    second = client.post(
        "/v1/onboarding/school",
        headers={"Authorization": f"Bearer {second_token}"},
        json=_onboard_payload(base + " "),
    )
    # Matches the existing (trimmed) record → not a new row.
    assert second.status_code == 409, second.text
    assert _count_schools(session, base, "ATA") == 1


def test_onboard_official_identifier_matches_authoritatively(
    client, session, test_user_account, test_user_account_token
):
    """An official identifier is an authoritative match even if the submitted
    name differs — it attaches to the existing school rather than duplicating."""
    official = f"OFF-{secrets.token_hex(4)}"
    school = _make_school(
        session,
        name=f"Official Registered Name {secrets.token_hex(4)}",
        country_code="ATA",
        state=SchoolState.INACTIVE,
        official_identifier=official,
    )
    resp = client.post(
        "/v1/onboarding/school",
        headers={"Authorization": f"Bearer {test_user_account_token}"},
        json=_onboard_payload(
            f"A Differently Typed Name {secrets.token_hex(4)}",
            official_identifier=official,
        ),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["school_wriveted_id"] == str(school.wriveted_identifier)
