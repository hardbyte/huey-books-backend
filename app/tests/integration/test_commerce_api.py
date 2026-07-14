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

    assert webhook_response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


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
