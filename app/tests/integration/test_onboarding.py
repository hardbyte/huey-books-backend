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
        info={"location": {"state": "Test", "postcode": "1234"}, "experiments": get_experiments({})},
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
        info={"location": {"state": "Test", "postcode": "1234"}, "experiments": get_experiments({})},
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
    events = session.query(Event).filter(
        Event.title == "School onboarding request"
    ).all()
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

        user = fresh_session.query(User).get(user_id)
        assert user is not None
        assert user.type == UserAccountType.SCHOOL_ADMIN
