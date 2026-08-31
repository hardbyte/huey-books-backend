from datetime import datetime, timedelta

from scripts.reconcile_school_billing import (
    PaidSubscriptionEvidence,
    is_reconciled_subscription_active,
    paid_subscription_evidence,
)


def test_paid_subscription_evidence_requires_paid_invoice():
    subscription = {
        "latest_invoice": {"status": "open", "created": 1_800_000_000},
        "current_period_end": 1_900_000_000,
    }

    assert paid_subscription_evidence(subscription) is None


def test_paid_subscription_evidence_uses_verified_invoice_and_period():
    subscription = {
        "status": "active",
        "collection_method": "charge_automatically",
        "latest_invoice": {
            "status": "paid",
            "created": 1_800_000_000,
            "status_transitions": {"paid_at": 1_800_000_100},
        },
        "current_period_end": 1_900_000_000,
    }

    evidence = paid_subscription_evidence(subscription)

    assert evidence is not None
    assert evidence.paid_at == datetime.utcfromtimestamp(1_800_000_100)
    assert evidence.period_end == datetime.utcfromtimestamp(1_900_000_000)
    assert evidence.stripe_status == "active"
    assert evidence.collection_method == "charge_automatically"


def test_paid_subscription_evidence_requires_a_paid_timestamp():
    subscription = {
        "latest_invoice": {"status": "paid"},
        "current_period_end": 1_900_000_000,
    }

    assert paid_subscription_evidence(subscription) is None


def test_canceled_but_paid_through_is_still_active():
    now = datetime(2026, 1, 1)
    evidence = PaidSubscriptionEvidence(
        paid_at=now - timedelta(days=10),
        period_end=now + timedelta(days=30),
        stripe_status="canceled",
        collection_method="send_invoice",
    )

    assert is_reconciled_subscription_active(evidence, now=now) is True


def test_canceled_and_period_elapsed_is_inactive():
    now = datetime(2026, 1, 1)
    evidence = PaidSubscriptionEvidence(
        paid_at=now - timedelta(days=400),
        period_end=now - timedelta(days=1),
        stripe_status="canceled",
        collection_method="send_invoice",
    )

    assert is_reconciled_subscription_active(evidence, now=now) is False


def test_active_status_is_active_regardless_of_period():
    now = datetime(2026, 1, 1)
    evidence = PaidSubscriptionEvidence(
        paid_at=now - timedelta(days=1),
        period_end=now - timedelta(days=1),
        stripe_status="active",
        collection_method="charge_automatically",
    )

    assert is_reconciled_subscription_active(evidence, now=now) is True
