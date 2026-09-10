from unittest.mock import patch
from uuid import uuid4

import pytest

from app.models.school_billing import StripeEventReceipt
from app.services.stripe_events import process_stripe_event


@pytest.mark.parametrize("event_type", ["invoice_payment.paid", "future.event"])
def test_unhandled_customerless_event_is_acknowledged_and_deduplicated(
    session, event_type
):
    event_id = f"evt_{uuid4().hex}"
    payload = {
        "id": "inpay_example",
        "object": "invoice_payment",
        "invoice": "in_example",
        "status": "paid",
        "amount_paid": 500000,
        "currency": "inr",
    }

    with patch("app.services.stripe_events.StripeCustomer.retrieve") as retrieve:
        assert process_stripe_event(event_type, payload, event_id=event_id) == {
            "status": "processed"
        }
        assert process_stripe_event(event_type, payload, event_id=event_id) == {
            "status": "duplicate"
        }

    retrieve.assert_not_called()
    assert session.get(StripeEventReceipt, event_id).event_type == event_type


def test_supported_event_failure_rolls_back_receipt_for_retry(session):
    event_id = f"evt_{uuid4().hex}"
    with pytest.raises(NotImplementedError, match="does not include a customer"):
        process_stripe_event(
            "invoice.upcoming",
            {"id": "in_example", "object": "invoice"},
            event_id=event_id,
        )
    assert session.get(StripeEventReceipt, event_id) is None


@pytest.mark.asyncio
async def test_invoice_payment_task_returns_200(internal_async_client):
    envelope = {
        "event_id": f"evt_{uuid4().hex}",
        "created": 1788956524,
        "api_version": "2026-08-26.dahlia",
        "stripe_event_type": "invoice_payment.paid",
        "stripe_event_data": {
            "id": "inpay_example",
            "object": "invoice_payment",
            "invoice": "in_example",
            "status": "paid",
            "payment": {"type": "payment_intent", "payment_intent": "pi_example"},
        },
    }
    with patch("app.api.internal.trigger_email_delivery"):
        first = await internal_async_client.post(
            "/v1/process-stripe-event", json=envelope
        )
        repeated = await internal_async_client.post(
            "/v1/process-stripe-event", json=envelope
        )

    assert first.status_code == 200
    assert first.json() == {"status": "processed"}
    assert repeated.status_code == 200
    assert repeated.json() == {"status": "duplicate"}
