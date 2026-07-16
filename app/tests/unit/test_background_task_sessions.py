import os
from datetime import datetime
from unittest.mock import MagicMock, Mock, patch
from uuid import uuid4

import pytest

os.environ.setdefault("POSTGRESQL_PASSWORD", "test-password")
os.environ.setdefault("SENDGRID_API_KEY", "test-sendgrid-key")
os.environ.setdefault("SHOPIFY_HMAC_SECRET", "test-shopify-secret")
os.environ.setdefault("SECRET_KEY", "test-secret-key")

from app.api import commerce as commerce_api
from app.models.service_account import ServiceAccount, ServiceAccountType
from app.schemas.sendgrid import CustomSendGridContactData, SendGridContactData
from app.schemas.shopify import ShopifyEventRoot
from app.services import background_events
from app.services import commerce as commerce_service


def _session_maker_with_context():
    session = Mock(name="fresh_session")
    context = MagicMock(name="session_context")
    context.__enter__.return_value = session
    session_maker = Mock(name="session_maker", return_value=context)
    return session_maker, context, session


@pytest.mark.asyncio
async def test_upsert_contact_schedules_session_owning_background_task():
    account = ServiceAccount(
        id=uuid4(),
        name="backend",
        type=ServiceAccountType.BACKEND,
    )
    background_tasks = Mock()
    data = SendGridContactData(email="reader@example.com")

    response = await commerce_api.upsert_contact(
        data=data,
        background_tasks=background_tasks,
        custom_fields=None,
        account=account,
        increment_children=True,
        sg=Mock(name="sendgrid"),
    )

    assert response.status_code == 202
    background_tasks.add_task.assert_called_once()
    func, payload, account_type, account_id, increment_children = (
        background_tasks.add_task.call_args.args
    )
    assert func is commerce_api.upsert_sendgrid_contact_background
    assert isinstance(payload, CustomSendGridContactData)
    assert payload.email == data.email
    assert payload.custom_fields is None
    assert account_type == "service_account"
    assert account_id == account.id
    assert increment_children is True


@pytest.mark.asyncio
async def test_shopify_order_schedules_session_owning_background_task():
    background_tasks = Mock()
    data = ShopifyEventRoot(
        id=123,
        email="buyer@example.com",
        created_at=datetime(2026, 1, 1),
        customer=None,
        total_price="42.00",
    )

    response = await commerce_api.create_shopify_order(
        data=data,
        background_tasks=background_tasks,
    )

    assert response.status_code == 200
    background_tasks.add_task.assert_called_once_with(
        commerce_api.process_shopify_order_background, data
    )


def test_upsert_sendgrid_contact_background_opens_fresh_session():
    session_maker, context, fresh_session = _session_maker_with_context()
    account_id = uuid4()
    account = Mock(name="account")
    payload = CustomSendGridContactData(email="reader@example.com", custom_fields=None)
    sg = Mock(name="sendgrid")

    with (
        patch.object(
            commerce_service, "get_session_maker", return_value=session_maker
        ) as get_session_maker,
        patch.object(
            commerce_service, "load_account_ref", return_value=account
        ) as load_account_ref,
        patch.object(commerce_service, "get_sendgrid_api", return_value=sg),
        patch.object(commerce_service, "upsert_sendgrid_contact") as upsert_contact,
    ):
        commerce_service.upsert_sendgrid_contact_background(
            payload, "service_account", account_id, True
        )

    get_session_maker.assert_called_once_with()
    session_maker.assert_called_once_with()
    load_account_ref.assert_called_once_with(
        fresh_session, "service_account", account_id
    )
    upsert_contact.assert_called_once_with(payload, fresh_session, account, sg, True)
    context.__exit__.assert_called_once()


def test_shopify_order_background_opens_fresh_session():
    session_maker, context, fresh_session = _session_maker_with_context()
    data = Mock(name="shopify_order")
    sg = Mock(name="sendgrid")

    with (
        patch.object(
            commerce_service, "get_session_maker", return_value=session_maker
        ) as get_session_maker,
        patch.object(commerce_service, "get_sendgrid_api", return_value=sg),
        patch.object(commerce_service, "process_shopify_order") as process_order,
    ):
        commerce_service.process_shopify_order_background(data)

    get_session_maker.assert_called_once_with()
    session_maker.assert_called_once_with()
    process_order.assert_called_once_with(data, sg, fresh_session)
    context.__exit__.assert_called_once()


def test_booklist_collection_comparison_background_event_opens_fresh_session():
    session_maker, context, fresh_session = _session_maker_with_context()
    account_id = uuid4()
    account = Mock(name="account")

    with (
        patch.object(
            background_events, "get_session_maker", return_value=session_maker
        ) as get_session_maker,
        patch.object(
            background_events, "load_account_ref", return_value=account
        ) as load_account_ref,
        patch.object(background_events.event_repository, "create") as create_event,
    ):
        background_events.record_booklist_collection_comparison_event(
            "collection-1", "booklist-1", 3, "user", account_id
        )

    get_session_maker.assert_called_once_with()
    session_maker.assert_called_once_with()
    load_account_ref.assert_called_once_with(fresh_session, "user", account_id)
    create_event.assert_called_once_with(
        session=fresh_session,
        title="Compared booklist and collection",
        info={
            "items_in_common": 3,
            "collection_id": "collection-1",
            "booklist_id": "booklist-1",
        },
        account=account,
    )
    context.__exit__.assert_called_once()
