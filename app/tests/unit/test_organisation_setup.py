from datetime import datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.organisation import OrganisationSubscription
from app.models.school import School, SchoolKind
from app.models.subscription import Subscription, SubscriptionType
from app.schemas.organisation_setup import (
    OrganisationSetupInput,
    OrganisationSetupResult,
)
from app.services import idempotency, organisation_setup
from app.services.workspace_errors import WorkspaceConflict


def subscription(**changes) -> Subscription:
    defaults = dict(
        id=f"sub_{uuid4()}",
        school_id=uuid4(),
        type=SubscriptionType.SCHOOL,
        stripe_customer_id="cus_test",
        is_active=True,
        paid_at=datetime.utcnow(),
        expiration=datetime.utcnow() + timedelta(days=30),
        product_id="prod_test",
    )
    return Subscription(**{**defaults, **changes})


def library(**changes) -> School:
    return School(**{"name": "Example School", "kind": SchoolKind.SCHOOL, **changes})


def payload(**changes) -> OrganisationSetupInput:
    return OrganisationSetupInput(
        **{
            "request_id": uuid4(),
            "expected_library_name": "Example School",
            "organisation_name": "Example Group",
            "new_library_name": "Senior library",
            "manager_ids": [uuid4()],
            **changes,
        }
    )


@pytest.fixture
def repository(monkeypatch):
    stub = type("Stub", (), {})()
    stub.library_subscriptions = AsyncMock(return_value=[])
    stub.subscription_owner = AsyncMock(return_value=None)
    monkeypatch.setattr(organisation_setup, "repository", stub)
    return stub


@pytest.mark.parametrize(
    "case,expected",
    [
        ("none", "no_paid_subscription"),
        ("unpaid", "no_paid_subscription"),
        ("expired", "no_paid_subscription"),
        ("cancelled", "no_paid_subscription"),
        ("two_paid", "multiple_paid_subscriptions"),
        ("owned", "subscription_already_owned"),
        ("eligible", None),
    ],
)
async def test_eligibility_reports_the_reason_setup_cannot_proceed(
    repository, case, expected
):
    candidates = {
        "none": [],
        "unpaid": [subscription(paid_at=None)],
        "expired": [subscription(expiration=datetime.utcnow() - timedelta(days=1))],
        "cancelled": [subscription(is_active=False)],
        "two_paid": [subscription(), subscription()],
        "owned": [subscription()],
        "eligible": [subscription()],
    }[case]
    repository.library_subscriptions.return_value = candidates
    if case == "owned":
        repository.subscription_owner.return_value = OrganisationSubscription()

    chosen, ineligibility = await organisation_setup.eligible_subscription(
        AsyncMock(), library()
    )

    assert (ineligibility.code if ineligibility else None) == expected
    assert (chosen is not None) == (expected is None)
    if ineligibility:
        assert ineligibility.message


async def test_a_grouped_library_is_ineligible_without_reading_subscriptions(
    repository,
):
    _, ineligibility = await organisation_setup.eligible_subscription(
        AsyncMock(), library(organisation_id=uuid4())
    )

    assert ineligibility.code == "already_grouped"
    repository.library_subscriptions.assert_not_awaited()


def test_setup_refuses_a_library_row_and_a_stale_name():
    with pytest.raises(WorkspaceConflict) as grouped:
        organisation_setup._require_current_review(
            library(kind=SchoolKind.LIBRARY), payload()
        )
    assert grouped.value.detail["code"] == "not_an_education_unit"

    with pytest.raises(WorkspaceConflict) as stale:
        organisation_setup._require_current_review(library(name="Renamed"), payload())
    assert stale.value.detail["code"] == "stale_review"

    organisation_setup._require_current_review(library(), payload())


@pytest.mark.parametrize(
    "changes,message",
    [
        ({"new_library_name": "example school"}, "different name"),
        ({"manager_ids": []}, "at least 1 item"),
        ({"librarian": {"name": "A", "email": "not-an-email"}}, "email address"),
        ({"expected_library_name": ""}, "at least 1 character"),
    ],
)
def test_setup_input_rejects_unusable_requests(changes, message):
    with pytest.raises(ValidationError, match=message):
        payload(**changes)


def test_setup_input_rejects_a_colleague_chosen_twice():
    repeated = uuid4()
    with pytest.raises(ValidationError, match="each organisation manager once"):
        payload(manager_ids=[repeated, repeated])


def test_setup_input_rejects_unknown_fields():
    with pytest.raises(ValidationError, match="Extra inputs"):
        payload(jokes_enabled=True)


def test_fingerprint_binds_a_request_key_to_its_payload_and_resource():
    data = payload()
    library_uuid, other_uuid = str(uuid4()), str(uuid4())
    digest = idempotency.fingerprint("setup", library_uuid, data)

    assert digest == idempotency.fingerprint("setup", library_uuid, data)
    assert digest != idempotency.fingerprint("setup", other_uuid, data)
    assert digest != idempotency.fingerprint("other", library_uuid, data)
    assert digest != idempotency.fingerprint(
        "setup",
        library_uuid,
        payload(request_id=data.request_id, organisation_name="B"),
    )


async def test_replay_returns_the_first_result_and_rejects_a_reused_key(monkeypatch):
    actor_id = uuid4()
    stored = OrganisationSetupResult(
        organisation_uuid=uuid4(),
        existing_library_uuid=uuid4(),
        new_library_uuid=uuid4(),
    )
    record = type(
        "Record",
        (),
        {
            "actor_id": actor_id,
            "fingerprint": "digest",
            "response": stored.model_dump(mode="json"),
        },
    )()
    stub = type("Stub", (), {})()
    stub.lock = AsyncMock()
    stub.get = AsyncMock(return_value=record)
    monkeypatch.setattr(idempotency, "repository", stub)

    async def replay(actor, digest):
        return await idempotency.replay(
            AsyncMock(), "setup", uuid4(), actor, digest, OrganisationSetupResult
        )

    assert await replay(actor_id, "digest") == stored
    for actor, digest in ((uuid4(), "digest"), (actor_id, "changed")):
        with pytest.raises(WorkspaceConflict) as conflict:
            await replay(actor, digest)
        assert conflict.value.detail["code"] == "idempotency_key_reused"


async def test_replay_is_none_for_a_first_attempt(monkeypatch):
    stub = type("Stub", (), {})()
    stub.lock = AsyncMock()
    stub.get = AsyncMock(return_value=None)
    monkeypatch.setattr(idempotency, "repository", stub)

    assert (
        await idempotency.replay(
            AsyncMock(), "setup", uuid4(), uuid4(), "digest", OrganisationSetupResult
        )
        is None
    )
    stub.lock.assert_awaited_once()
