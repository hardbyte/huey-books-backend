"""Integration tests for the school referral invitation flow."""

from datetime import datetime, timedelta

from sqlalchemy import select

from app.models.product import Product
from app.models.school import School, SchoolBookbotType, SchoolState
from app.models.school_invitation import SchoolInvitation, SchoolInvitationStatus
from app.models.subscription import Subscription, SubscriptionType


def _make_paying(session, school):
    """Make a school ACTIVE with a real (paying) Stripe subscription."""
    school.state = SchoolState.ACTIVE
    session.add(school)
    session.add(Product(id="price_inviter_test", name="Supporter School"))
    session.flush()
    session.add(
        Subscription(
            id=f"sub_inviter_{school.id}",
            product_id="price_inviter_test",
            stripe_customer_id="cus_inviter",
            school_id=school.wriveted_identifier,
            type=SubscriptionType.SCHOOL,
            is_active=True,
            expiration=datetime(2099, 1, 1),
        )
    )
    session.commit()


def _make_invited_school(session) -> School:
    school = School(
        name=f"Invited School {datetime.utcnow().timestamp()}",
        country_code="AUS",
        state=SchoolState.INACTIVE,
        bookbot_type=SchoolBookbotType.HUEY_BOOKS,
    )
    session.add(school)
    session.commit()
    session.refresh(school)
    return school


def test_send_requires_paying_inviter(
    client, test_school, admin_of_test_school_headers
):
    """A non-paying school cannot send invitations."""
    resp = client.post(
        f"/v1/school/{test_school.wriveted_identifier}/invitations",
        headers=admin_of_test_school_headers,
        json={
            "invited_school_name": "Somewhere Primary",
            "country_code": "AUS",
            "contact_email": "prin@somewhere.example",
        },
    )
    assert resp.status_code == 403, resp.text


def test_send_and_accept_grants_free_access(
    client,
    session,
    session_factory,
    test_school,
    admin_of_test_school_headers,
    test_user_account,
    test_user_account_token,
):
    """Paying inviter sends → invited public user accepts → school ACTIVE + comp grant."""
    _make_paying(session, test_school)
    invited = _make_invited_school(session)
    invited_wid = invited.wriveted_identifier

    send = client.post(
        f"/v1/school/{test_school.wriveted_identifier}/invitations",
        headers=admin_of_test_school_headers,
        json={
            "invited_school_wriveted_id": str(invited_wid),
            "contact_email": "newadmin@invited.example",
            "grant_days": 90,
        },
    )
    assert send.status_code == 201, send.text
    assert send.json()["status"] == "sent"

    with session_factory() as s:
        inv = s.execute(
            select(SchoolInvitation).where(
                SchoolInvitation.invited_school_id == invited_wid
            )
        ).scalars().first()
        assert inv is not None
        token = inv.token

    accept = client.post(
        f"/v1/invitations/{token}/accept",
        headers={"Authorization": f"Bearer {test_user_account_token}"},
    )
    assert accept.status_code == 200, accept.text
    body = accept.json()
    assert body["school_wriveted_id"] == str(invited_wid)
    assert body["access_until"] is not None

    with session_factory() as s:
        inv = s.execute(
            select(SchoolInvitation).where(
                SchoolInvitation.invited_school_id == invited_wid
            )
        ).scalars().first()
        assert inv.status == SchoolInvitationStatus.ACCEPTED
        school = s.execute(
            select(School).where(School.wriveted_identifier == invited_wid)
        ).scalars().first()
        assert school.state == SchoolState.ACTIVE
        grant = s.execute(
            select(Subscription).where(
                Subscription.id == f"comp_invite_{invited_wid}"
            )
        ).scalars().first()
        assert grant is not None and grant.is_active
        assert grant.expiration > datetime.utcnow() + timedelta(days=80)


def test_staff_can_invite_any_school_without_source(
    client, test_wrivetedadmin_account_headers
):
    """Staff (Wriveted) can invite a brand-new school with no source school."""
    resp = client.post(
        "/v1/admin/invitations",
        headers=test_wrivetedadmin_account_headers,
        json={
            "invited_school_name": f"Staff Invited {datetime.utcnow().timestamp()}",
            "country_code": "AUS",
            "contact_email": f"staff-invite-{datetime.utcnow().timestamp()}@x.example",
            "message": "Come and join us!",
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["status"] == "sent"


def test_school_admin_cannot_use_staff_invite(
    client, admin_of_test_school_headers
):
    """The staff invite endpoint is superuser-only."""
    resp = client.post(
        "/v1/admin/invitations",
        headers=admin_of_test_school_headers,
        json={
            "invited_school_name": "Nope",
            "country_code": "AUS",
            "contact_email": "nope@x.example",
        },
    )
    assert resp.status_code in (401, 403), resp.text


def test_allowance_and_staff_bonus(
    client,
    session,
    test_school,
    admin_of_test_school_headers,
    test_wrivetedadmin_account_headers,
):
    """Allowance reflects the base cap and staff-granted bonuses."""
    _make_paying(session, test_school)
    wid = test_school.wriveted_identifier

    allowance = client.get(
        f"/v1/school/{wid}/invitations/allowance",
        headers=admin_of_test_school_headers,
    )
    assert allowance.status_code == 200, allowance.text
    base_total = allowance.json()["total"]

    granted = client.post(
        f"/v1/admin/schools/{wid}/invitations/grant-bonus",
        headers=test_wrivetedadmin_account_headers,
        json={"additional": 5},
    )
    assert granted.status_code == 200, granted.text
    assert granted.json()["staff_bonus"] == 5
    assert granted.json()["total"] == base_total + 5


def test_cannot_invite_active_school(
    client, session, test_school, admin_of_test_school_headers
):
    """A school already on Huey can't be invited."""
    _make_paying(session, test_school)
    other = _make_invited_school(session)
    other.state = SchoolState.ACTIVE
    session.add(other)
    session.commit()

    resp = client.post(
        f"/v1/school/{test_school.wriveted_identifier}/invitations",
        headers=admin_of_test_school_headers,
        json={
            "invited_school_wriveted_id": str(other.wriveted_identifier),
            "contact_email": "x@active.example",
        },
    )
    assert resp.status_code == 409, resp.text
