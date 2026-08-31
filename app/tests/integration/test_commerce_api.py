from sqlalchemy import text
from starlette import status


def test_sendgrid_email_endpoint_commits_outbox_row(
    client, backend_service_account, backend_service_account_headers, session
):
    """The /sendgrid/email endpoint must commit the queued outbox row.

    publish_event_sync only adds the row; if the endpoint doesn't commit, the
    request session rolls it back on teardown and the email is silently lost.
    """

    def email_count() -> int:
        session.rollback()  # see only committed state
        return session.execute(
            text(
                "SELECT COUNT(*) FROM event_outbox WHERE event_type='email_notification'"
            )
        ).scalar()

    before = email_count()
    resp = client.post(
        "/v1/sendgrid/email",
        headers=backend_service_account_headers,
        json={"to_emails": ["someone@example.com"], "subject": "Hi there"},
    )
    assert resp.status_code == 202
    assert email_count() == before + 1


def test_stripe_webhook_requires_stripe_sig_header(
    client,
    session_factory,
    backend_service_account,
    backend_service_account_headers,
):
    webhook_response = client.post(
        "/v1/stripe/webhook",
        json={
            "title": "Test",
            "description": "original description",
            "level": "warning",
        },
        headers={},
    )

    assert webhook_response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


def test_stripe_webhook_validates_signature(
    client,
    session_factory,
    backend_service_account,
    backend_service_account_headers,
):
    webhook_response = client.post(
        "/v1/stripe/webhook",
        json={
            "title": "Test",
            "description": "original description",
            "level": "warning",
        },
        headers={"stripe-signature": "t=123,v1=abc,v0=def,invalid-signature=123"},
    )

    assert webhook_response.status_code == status.HTTP_400_BAD_REQUEST


def test_stripe_webhook_queues_serializable_payload_for_price_event(client):
    """A price.updated event carries a Stripe Price object. The Cloud Tasks payload
    is json.dumps'd, and a raw Price is not JSON-serializable (regression: this
    threw TypeError and returned 500), so the handler must hand off a plain dict.
    """
    import json
    from unittest.mock import patch

    import stripe

    from app.api.dependencies.stripe_security import get_stripe_event
    from app.main import app

    price = stripe.Price.construct_from(
        {
            "id": "price_x",
            "object": "price",
            "unit_amount": 500000,
            "currency": "inr",
            "recurring": {"interval": "year", "interval_count": 1},
        },
        "sk_test",
    )
    event = stripe.Event.construct_from(
        {
            "id": "evt_x",
            "type": "price.updated",
            "api_version": "2026-08-26.dahlia",
            "created": 1,
            "data": {"object": price},
        },
        "sk_test",
    )

    captured = {}

    def fake_queue(name, payload):
        captured["payload"] = payload
        return object()

    app.dependency_overrides[get_stripe_event] = lambda: event
    try:
        with patch(
            "app.api.commerce.queue_background_task", side_effect=fake_queue
        ):
            resp = client.post("/v1/stripe/webhook", json={})
    finally:
        app.dependency_overrides.pop(get_stripe_event, None)

    assert resp.status_code == 200
    payload = captured["payload"]
    json.dumps(payload)  # would raise TypeError without the to_dict() conversion
    assert isinstance(payload["stripe_event_data"], dict)
    assert payload["stripe_event_data"]["unit_amount"] == 500000
    assert payload["stripe_event_data"]["recurring"]["interval"] == "year"
