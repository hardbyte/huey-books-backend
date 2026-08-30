import os
import subprocess
import sys
from unittest.mock import Mock, patch

from app.api.internal import handle_send_email
from app.schemas.feedback import SendEmailPayload
from app.schemas.sendgrid import SendGridEmailData
from app.services.email_notification import EmailType, trigger_email_delivery


def test_internal_api_imports_in_a_fresh_process():
    environment = {
        **os.environ,
        "POSTGRESQL_PASSWORD": "test",
        "SECRET_KEY": "test",
        "SHOPIFY_HMAC_SECRET": "test",
        "STRIPE_SECRET_KEY": "test",
    }

    result = subprocess.run(
        [sys.executable, "-c", "import app.internal_api"],
        capture_output=True,
        env=environment,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr


def test_internal_email_is_committed_and_delivery_is_nudged():
    session = Mock()
    payload = SendEmailPayload(
        email_data=SendGridEmailData(
            to_emails=["recipient@example.com"],
            subject="A useful update",
            html_content="<p>Hello</p>",
        ),
        user_id="8e65b657-bc15-4c3f-b06f-64fc99b9ae11",
    )

    with (
        patch("app.api.internal.send_email_reliable_sync") as queue_email,
        patch("app.api.internal.trigger_email_delivery") as nudge_delivery,
    ):
        result = handle_send_email(payload, session)

    queue_email.assert_called_once_with(
        db=session,
        email_data=payload.email_data,
        email_type=EmailType.SYSTEM,
        user_id=payload.user_id,
        service_account_id=None,
    )
    session.commit.assert_called_once_with()
    nudge_delivery.assert_called_once_with()
    assert result == {"msg": "Email queued for reliable delivery"}


def test_delivery_nudge_failure_is_best_effort():
    with patch(
        "app.services.background_tasks.queue_background_task",
        side_effect=RuntimeError("task queue unavailable"),
    ):
        trigger_email_delivery()
