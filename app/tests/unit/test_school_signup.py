"""Unit tests for school signup emails and Stripe checkout session creation."""

import uuid

import pytest

from app.models.school import School
from app.services import school_billing
from app.services.school_emails import (
    render_school_activated_html,
    render_school_registered_html,
    render_school_staff_invite_html,
    render_staff_new_school_alert_html,
)

# --- email renderers ---


def test_school_registered_html_mentions_subscription_and_name():
    html = render_school_registered_html("St Kilda Primary", "Sarah")
    assert "St Kilda Primary" in html
    assert "Sarah" in html
    assert "subscription" in html.lower()


def test_school_activated_html_no_contact_name_falls_back():
    html = render_school_activated_html("St Kilda Primary", None)
    assert "live" in html.lower()
    assert "Hi there" in html


def test_staff_invite_html_links_to_admin():
    html = render_school_staff_invite_html(
        "St Kilda Primary", "Alex", "https://admin.example/x"
    )
    assert "St Kilda Primary" in html
    assert "Alex" in html
    assert "https://admin.example/x" in html


def test_staff_alert_lists_details_and_escapes():
    html = render_staff_new_school_alert_html(
        school_name="<b>Hack</b> School",
        wriveted_id="w-123",
        contact_name="Alex",
        contact_email="alex@school.example",
        contact_role="teacher",
        country_code="AUS",
        student_count_estimate=200,
        message=None,
    )
    assert "alex@school.example" in html
    assert "w-123" in html
    assert "&lt;b&gt;Hack&lt;/b&gt;" in html  # escaped
    assert "<b>Hack</b>" not in html  # not raw


# --- checkout session ---


def _school() -> School:
    return School(
        name="Test School",
        wriveted_identifier=uuid.uuid4(),
        info={"onboarding": {"contact_email": "contact@school.example"}},
    )


@pytest.mark.asyncio
async def test_create_checkout_session_builds_expected_params(monkeypatch):
    monkeypatch.setattr(
        school_billing.settings, "STRIPE_SCHOOL_PRICE_IDS", ["price_school"]
    )
    monkeypatch.setattr(school_billing.settings, "STRIPE_SECRET_KEY", "sk_test")
    monkeypatch.setattr(
        school_billing.settings, "HUEY_BOOKS_APP_URL", "https://hueybooks.com"
    )

    captured = {}

    class FakeSession:
        id = "cs_test_123"
        url = "https://checkout.stripe.com/pay/cs_test_123"

    def fake_create(**kwargs):
        captured.update(kwargs)
        return FakeSession()

    monkeypatch.setattr(school_billing.stripe.checkout.Session, "create", fake_create)

    school = _school()
    url = await school_billing.create_school_checkout_session(school)

    assert url == "https://checkout.stripe.com/pay/cs_test_123"
    assert captured["mode"] == "subscription"
    assert captured["line_items"] == [{"price": "price_school", "quantity": 1}]
    assert captured["client_reference_id"] == str(school.wriveted_identifier)
    assert captured["customer_email"] == "contact@school.example"
    assert captured["success_url"].startswith("https://hueybooks.com")
    assert "{CHECKOUT_SESSION_ID}" in captured["success_url"]


@pytest.mark.asyncio
async def test_create_checkout_session_requires_price_id(monkeypatch):
    monkeypatch.setattr(school_billing.settings, "STRIPE_SCHOOL_PRICE_IDS", [])
    with pytest.raises(school_billing.SchoolBillingError):
        await school_billing.create_school_checkout_session(School(name="X"))


@pytest.mark.asyncio
async def test_create_checkout_session_rejects_unknown_price_id(monkeypatch):
    monkeypatch.setattr(
        school_billing.settings, "STRIPE_SCHOOL_PRICE_IDS", ["price_school"]
    )
    monkeypatch.setattr(school_billing.settings, "STRIPE_SECRET_KEY", "sk_test")
    with pytest.raises(school_billing.SchoolBillingError):
        await school_billing.create_school_checkout_session(_school(), price_id="price_other")
