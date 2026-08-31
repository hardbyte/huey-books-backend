"""Initialize historical school subscriptions from paid Stripe invoices."""

import argparse
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import stripe
from sqlalchemy import select

from app.config import get_settings
from app.db.session import get_session_maker
from app.models.subscription import Subscription, SubscriptionType
from app.services.school_access import lock_school_access_sync
from app.services.school_billing_status import recompute_school_access_sync


@dataclass(frozen=True)
class PaidSubscriptionEvidence:
    paid_at: datetime
    period_end: datetime
    stripe_status: str | None
    collection_method: str | None


def _stripe_value(value, key: str, default=None):
    if hasattr(value, "get"):
        return value.get(key, default)
    return default


def _as_datetime(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        return datetime.utcfromtimestamp(value)
    return None


def paid_subscription_evidence(stripe_subscription) -> PaidSubscriptionEvidence | None:
    invoice = _stripe_value(stripe_subscription, "latest_invoice")
    if not invoice or not hasattr(invoice, "get") or invoice.get("status") != "paid":
        return None

    paid_at = _as_datetime(
        (_stripe_value(invoice, "status_transitions") or {}).get("paid_at")
    ) or _as_datetime(_stripe_value(invoice, "created"))
    period_end = _as_datetime(_stripe_value(stripe_subscription, "current_period_end"))
    if period_end is None:
        items = (_stripe_value(stripe_subscription, "items") or {}).get("data") or []
        period_end = _as_datetime(items[0].get("current_period_end")) if items else None
    if paid_at is None or period_end is None:
        return None

    stripe_status = _stripe_value(stripe_subscription, "status")
    collection_method = _stripe_value(stripe_subscription, "collection_method")
    return PaidSubscriptionEvidence(
        paid_at=paid_at,
        period_end=period_end,
        stripe_status=stripe_status if isinstance(stripe_status, str) else None,
        collection_method=(
            collection_method if isinstance(collection_method, str) else None
        ),
    )


def is_reconciled_subscription_active(
    evidence: PaidSubscriptionEvidence, *, now: datetime | None = None
) -> bool:
    """Whether a reconciled subscription should count as active.

    ``evidence`` is only produced when the latest invoice is paid, so a
    subscription that Stripe has ``canceled`` but whose paid period has not yet
    elapsed still entitles the school until ``period_end``. Otherwise fall back
    to the live Stripe statuses.
    """
    if evidence.stripe_status in {"active", "past_due", "trialing"}:
        return True
    return evidence.period_end > (now or datetime.utcnow())


def _candidate_subscriptions(school_id: UUID | None) -> list[tuple[str, UUID]]:
    Session = get_session_maker()
    with Session() as session:
        statement = select(Subscription.id, Subscription.school_id).where(
            Subscription.type == SubscriptionType.SCHOOL,
            Subscription.school_id.is_not(None),
            Subscription.stripe_customer_id != "",
            Subscription.paid_at.is_(None),
        )
        if school_id is not None:
            statement = statement.where(Subscription.school_id == school_id)
        return [
            (subscription_id, row_school_id)
            for subscription_id, row_school_id in session.execute(statement)
        ]


def reconcile(*, apply: bool, school_id: UUID | None) -> int:
    settings = get_settings()
    if not settings.STRIPE_SECRET_KEY:
        raise RuntimeError("STRIPE_SECRET_KEY is not configured")
    stripe.api_key = settings.STRIPE_SECRET_KEY

    candidates = _candidate_subscriptions(school_id)
    reconciled = 0
    skipped = 0
    failed = 0
    Session = get_session_maker()

    for subscription_id, candidate_school_id in candidates:
        try:
            stripe_subscription = stripe.Subscription.retrieve(
                subscription_id, expand=["latest_invoice"]
            )
            latest_invoice = _stripe_value(stripe_subscription, "latest_invoice")
            if isinstance(latest_invoice, str):
                stripe_subscription["latest_invoice"] = stripe.Invoice.retrieve(
                    latest_invoice
                )
            evidence = paid_subscription_evidence(stripe_subscription)
            if evidence is None:
                skipped += 1
                print(f"SKIP {subscription_id}: no paid latest invoice")
                continue
            if not apply:
                reconciled += 1
                print(
                    f"WOULD RECONCILE {subscription_id}: paid through "
                    f"{evidence.period_end.isoformat()}"
                )
                continue

            with Session() as session:
                school = lock_school_access_sync(session, candidate_school_id)
                if school is None:
                    skipped += 1
                    continue
                subscription = session.execute(
                    select(Subscription)
                    .where(Subscription.id == subscription_id)
                    .with_for_update()
                ).scalar_one_or_none()
                if subscription is None or subscription.paid_at is not None:
                    skipped += 1
                    continue
                subscription.paid_at = evidence.paid_at
                subscription.expiration = evidence.period_end
                subscription.stripe_status = evidence.stripe_status
                subscription.collection_method = evidence.collection_method
                subscription.is_active = is_reconciled_subscription_active(evidence)
                recompute_school_access_sync(session, school)
                session.commit()
            reconciled += 1
            print(f"RECONCILED {subscription_id}")
        except Exception as error:
            failed += 1
            print(f"FAILED {subscription_id}: {error}")

    mode = "apply" if apply else "dry-run"
    print(
        f"{mode}: candidates={len(candidates)} reconciled={reconciled} "
        f"skipped={skipped} failed={failed}"
    )
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--school-id", type=UUID)
    arguments = parser.parse_args()
    return reconcile(apply=arguments.apply, school_id=arguments.school_id)


if __name__ == "__main__":
    raise SystemExit(main())
