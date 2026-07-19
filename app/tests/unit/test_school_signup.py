"""Unit tests for school signup emails and Stripe checkout session creation."""

import uuid

import pytest

from app.models.school import School
from app.services import school_billing
from app.services.school_emails import (
    render_contribution_thankyou_html,
    render_school_activated_html,
    render_school_contribution_notice_html,
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


def test_school_registered_html_includes_activate_cta_when_url_given():
    url = "https://hueybooks.com/school/activate"
    html = render_school_registered_html("St Kilda Primary", "Sarah", activate_url=url)
    assert f'href="{url}"' in html
    assert "Activate your school" in html
    # No dangling link when no URL is supplied.
    assert "href" not in render_school_registered_html("St Kilda Primary", "Sarah")


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


def test_contribution_thankyou_credit_path_mentions_credit():
    html = render_contribution_thankyou_html(
        "St Kilda Primary", "50.00 AUD", "credited"
    )
    assert "St Kilda Primary" in html
    assert "50.00 AUD" in html
    assert "credited" in html.lower()
    assert "live" not in html.lower()


def test_contribution_thankyou_activation_mentions_live_and_date():
    html = render_contribution_thankyou_html(
        "St Kilda Primary", "50.00 AUD", "activated", access_until="2026-08-15"
    )
    assert "live" in html.lower()
    assert "2026-08-15" in html


def test_contribution_thankyou_extension_mentions_extended_and_date():
    html = render_contribution_thankyou_html(
        "St Kilda Primary", "50.00 AUD", "extended", access_until="2026-09-14"
    )
    assert "extended" in html.lower()
    assert "2026-09-14" in html


def test_contribution_notice_escapes_and_names_school():
    html = render_school_contribution_notice_html(
        "<b>Hack</b> School", "Alex", "50.00 AUD", "credited"
    )
    assert "Alex" in html
    assert "&lt;b&gt;Hack&lt;/b&gt;" in html
    assert "<b>Hack</b>" not in html


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
        await school_billing.create_school_checkout_session(
            _school(), price_id="price_other"
        )


# --- contribution checkout session ---


@pytest.mark.asyncio
async def test_create_contribution_session_builds_expected_params(monkeypatch):
    monkeypatch.setattr(
        school_billing.settings,
        "STRIPE_SCHOOL_CONTRIBUTION_PRICE_IDS",
        ["price_contrib"],
    )
    monkeypatch.setattr(school_billing.settings, "STRIPE_SECRET_KEY", "sk_test")
    monkeypatch.setattr(
        school_billing.settings, "HUEY_BOOKS_APP_URL", "https://hueybooks.com"
    )

    captured = {}

    class FakeSession:
        id = "cs_contrib_123"
        url = "https://checkout.stripe.com/pay/cs_contrib_123"

    def fake_create(**kwargs):
        captured.update(kwargs)
        return FakeSession()

    monkeypatch.setattr(school_billing.stripe.checkout.Session, "create", fake_create)

    school = _school()
    url = await school_billing.create_school_contribution_checkout_session(school)

    assert url == "https://checkout.stripe.com/pay/cs_contrib_123"
    assert captured["mode"] == "payment"
    assert captured["line_items"] == [{"price": "price_contrib", "quantity": 1}]
    assert captured["client_reference_id"] == str(school.wriveted_identifier)
    assert captured["metadata"]["kind"] == "school_contribution"
    assert captured["metadata"]["wriveted_school_id"] == str(school.wriveted_identifier)
    assert "{CHECKOUT_SESSION_ID}" in captured["success_url"]


@pytest.mark.asyncio
async def test_create_contribution_session_requires_price_id(monkeypatch):
    monkeypatch.setattr(
        school_billing.settings, "STRIPE_SCHOOL_CONTRIBUTION_PRICE_IDS", []
    )
    with pytest.raises(school_billing.SchoolBillingError):
        await school_billing.create_school_contribution_checkout_session(_school())


@pytest.mark.asyncio
async def test_create_contribution_session_rejects_unknown_price_id(monkeypatch):
    monkeypatch.setattr(
        school_billing.settings,
        "STRIPE_SCHOOL_CONTRIBUTION_PRICE_IDS",
        ["price_contrib"],
    )
    monkeypatch.setattr(school_billing.settings, "STRIPE_SECRET_KEY", "sk_test")
    with pytest.raises(school_billing.SchoolBillingError):
        await school_billing.create_school_contribution_checkout_session(
            _school(), price_id="price_other"
        )
