"""Tests for the Resend email backend and the ported inline templates."""

from unittest.mock import Mock

import httpx
import pytest

from app.services import email_notification
from app.services.email_notification import EmailNotificationService
from app.services.email_templates import render_email_template

WELCOME_ID = "d-3655b189b9a8427d99fe02cf7e7f3fd9"
SUBSCRIPTION_ID = "d-fa829ecc76fc4e37ab4819abb6e0d188"
FEEDBACK_ID = "d-841938d74d9142509af934005ad6e3ed"


def _service() -> EmailNotificationService:
    return EmailNotificationService(Mock())


# --- template rendering ---


def test_render_known_templates_return_html():
    assert "Welcome to Huey Books" in render_email_template(WELCOME_ID, {"name": "Sam"})
    assert "Membership" in render_email_template(SUBSCRIPTION_ID, {"name": "Sam"})
    fb = render_email_template(
        FEEDBACK_ID,
        {
            "supporter_name": "Sam",
            "reader_name": "Alex",
            "book_title": "Matilda",
            "encoded_url": "https://hueybooks.com/reader-feedback/?t=abc",
        },
    )
    assert "Alex" in fb and "Matilda" in fb
    assert "https://hueybooks.com/reader-feedback/?t=abc" in fb  # CTA link present


def test_render_escapes_untrusted_values():
    html = render_email_template(WELCOME_ID, {"name": "<script>x</script>"})
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_render_unknown_template_returns_none():
    assert render_email_template("d-unknown", {"name": "x"}) is None


# --- Resend send path ---


def _mock_httpx(monkeypatch, handler):
    """Route the service's httpx.AsyncClient through a MockTransport."""
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )


@pytest.mark.asyncio
async def test_resend_send_builds_payload_and_succeeds(monkeypatch):
    monkeypatch.setattr(email_notification.config, "RESEND_API_KEY", "re_test")
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        import json

        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "email_123"})

    _mock_httpx(monkeypatch, handler)

    ok = await _service()._send_email_via_resend(
        {
            "from_email": "hello@hueybooks.com",
            "from_name": "Huey Books",
            "to_emails": ["someone@example.com"],
            "subject": "Hi",
            "html_content": "<p>Hello</p>",
            "reply_to": "hello@hueybooks.com",
            "headers": {"List-Unsubscribe": "<https://x/u>"},
        }
    )

    assert ok is True
    assert captured["url"] == "https://api.resend.com/emails"
    assert captured["auth"] == "Bearer re_test"
    body = captured["body"]
    assert body["from"] == "Huey Books <hello@hueybooks.com>"
    assert body["to"] == ["someone@example.com"]
    assert body["html"] == "<p>Hello</p>"
    assert body["reply_to"] == "hello@hueybooks.com"
    assert body["headers"]["List-Unsubscribe"] == "<https://x/u>"


@pytest.mark.asyncio
async def test_resend_renders_template_when_no_html(monkeypatch):
    monkeypatch.setattr(email_notification.config, "RESEND_API_KEY", "re_test")
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": "email_456"})

    _mock_httpx(monkeypatch, handler)

    ok = await _service()._send_email_via_resend(
        {
            "to_emails": ["parent@example.com"],
            "subject": "Your Huey Books Membership",
            "template_id": SUBSCRIPTION_ID,
            "template_data": {"name": "Sam"},
        }
    )
    assert ok is True
    assert "Membership" in captured["body"]["html"]


@pytest.mark.asyncio
async def test_resend_returns_false_without_key(monkeypatch):
    monkeypatch.setattr(email_notification.config, "RESEND_API_KEY", "")
    ok = await _service()._send_email_via_resend(
        {"to_emails": ["a@b.com"], "html_content": "<p>x</p>"}
    )
    assert ok is False


@pytest.mark.asyncio
async def test_resend_returns_false_on_non_2xx(monkeypatch):
    monkeypatch.setattr(email_notification.config, "RESEND_API_KEY", "re_test")
    _mock_httpx(monkeypatch, lambda request: httpx.Response(422, json={"error": "bad"}))
    ok = await _service()._send_email_via_resend(
        {"to_emails": ["a@b.com"], "subject": "x", "html_content": "<p>x</p>"}
    )
    assert ok is False


@pytest.mark.asyncio
async def test_dispatch_routes_to_resend(monkeypatch):
    monkeypatch.setattr(email_notification.config, "EMAIL_PROVIDER", "resend")
    monkeypatch.setattr(email_notification.config, "RESEND_API_KEY", "re_test")
    _mock_httpx(monkeypatch, lambda request: httpx.Response(200, json={"id": "x"}))
    ok = await _service()._send_email(
        {"to_emails": ["a@b.com"], "subject": "x", "html_content": "<p>x</p>"}
    )
    assert ok is True
