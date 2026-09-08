from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.models.subscription import Subscription
from app.services import school_billing_status


@pytest.mark.parametrize("paid", [False, True])
@pytest.mark.parametrize("synchronous", [False, True])
async def test_unconfigured_prices_preserve_read_only_status(
    monkeypatch, paid, synchronous
):
    monkeypatch.setattr(
        school_billing_status,
        "get_settings",
        lambda: SimpleNamespace(
            STRIPE_SCHOOL_PRICE_IDS=[],
            STRIPE_SCHOOL_PRICE_IDS_BY_COUNTRY={},
            INVOICE_FIRST_COUNTRY_CODES=[],
        ),
    )
    session = MagicMock() if synchronous else AsyncMock()
    result = MagicMock()
    result.scalars.return_value.first.return_value = None
    subscriptions = (
        [
            Subscription(
                id="sub_existing",
                is_active=True,
                stripe_customer_id="cus_existing",
                paid_at=datetime.utcnow(),
                expiration=datetime.utcnow() + timedelta(days=30),
                stripe_status="active",
            )
        ]
        if paid
        else []
    )
    result.scalars.return_value.__iter__.return_value = iter(subscriptions)
    session.execute.return_value = result
    session.get.return_value = object() if paid else None
    price_lookup = AsyncMock()
    monkeypatch.setattr(school_billing_status, "get_price_info", price_lookup)
    monkeypatch.setattr(school_billing_status, "get_price_info_sync", price_lookup)
    school = SimpleNamespace(wriveted_identifier=uuid4(), country_code="NZL")
    status = (
        school_billing_status.resolve_school_billing_status_sync(session, school)
        if synchronous
        else await school_billing_status.resolve_school_billing_status(session, school)
    )
    assert status.offer is None
    assert not status.capabilities.card
    assert not status.capabilities.invoice
    assert status.capabilities.blocking_reason == (
        "paid_subscription" if paid else "billing_not_configured"
    )
    assert status.entitlement.active is paid
    assert status.capabilities.manage is paid
    price_lookup.assert_not_called()


from datetime import datetime, timedelta
