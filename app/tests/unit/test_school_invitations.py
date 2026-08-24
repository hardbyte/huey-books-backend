"""Unit tests for school-invitation pure logic (no DB): request validation,
email rendering, and the invite grant constants."""

import pytest
from pydantic import ValidationError

from app.schemas.school_invitation import SchoolInvitationCreate
from app.services.school_access import (
    COMP_GRANT_SOURCES,
    INVITE_GRANT_SOURCE,
    invite_grant_id,
)
from app.services.school_emails import _grant_period_label, render_school_invite_html


def test_create_requires_a_target():
    # Neither an existing school id nor name+country → rejected.
    with pytest.raises(ValidationError):
        SchoolInvitationCreate(contact_email="a@b.com")


def test_create_accepts_existing_school():
    import uuid

    p = SchoolInvitationCreate(
        invited_school_wriveted_id=uuid.uuid4(), contact_email="a@b.com"
    )
    assert p.contact_email == "a@b.com"


def test_create_accepts_new_school_name_and_country():
    p = SchoolInvitationCreate(
        invited_school_name="New School", country_code="IND", contact_email="a@b.com"
    )
    assert p.invited_school_name == "New School"


def test_create_rejects_name_without_country():
    with pytest.raises(ValidationError):
        SchoolInvitationCreate(invited_school_name="No Country", contact_email="a@b.com")


def test_grant_period_label():
    assert _grant_period_label(90) == "3 months"
    assert _grant_period_label(30) == "1 month"
    assert _grant_period_label(45) == "45 days"


def test_invite_email_mentions_inviter_period_and_cta():
    html = render_school_invite_html(
        inviter_school_name="Melbourne Grammar",
        invited_school_name="Chennai School",
        accept_url="https://hueybooks.com/school/invited?token=abc",
        grant_days=90,
    )
    assert "Melbourne Grammar" in html
    assert "3 months" in html
    assert "https://hueybooks.com/school/invited?token=abc" in html
    assert "Activate your free trial" in html


def test_invite_grant_constants():
    assert INVITE_GRANT_SOURCE in COMP_GRANT_SOURCES
    assert "contribution_grant" in COMP_GRANT_SOURCES
    assert invite_grant_id("abc-123") == "comp_invite_abc-123"
