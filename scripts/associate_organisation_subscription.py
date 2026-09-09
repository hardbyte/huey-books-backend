"""Explicit subscription ownership assignment for migration/support operators."""

import argparse
from uuid import UUID

from sqlalchemy import select

from app.db.session import get_session_maker
from app.models.organisation import Organisation, OrganisationSubscription
from app.models.subscription import Subscription, SubscriptionType


def associate(session, organisation_id: UUID, subscription_id: str) -> bool:
    organisation = session.scalar(
        select(Organisation).where(Organisation.id == organisation_id).with_for_update()
    )
    if organisation is None:
        raise ValueError("Organisation not found")
    subscription = session.scalar(
        select(Subscription).where(Subscription.id == subscription_id).with_for_update()
    )
    if subscription is None:
        raise ValueError("Subscription not found")
    if subscription.parent_id is not None or subscription.type not in (
        SubscriptionType.SCHOOL,
        SubscriptionType.LIBRARY,
    ):
        raise ValueError(
            "Only school/library subscriptions can belong to an organisation"
        )
    existing = session.get(OrganisationSubscription, subscription_id)
    if existing is not None:
        if existing.organisation_id != organisation_id:
            raise ValueError(
                "Subscription already belongs to another organisation; ownership transfer requires separate review"
            )
        return False
    session.add(
        OrganisationSubscription(
            organisation_id=organisation_id, subscription_id=subscription_id
        )
    )
    session.flush()
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organisation", required=True, type=UUID)
    parser.add_argument("--subscription", required=True)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit the association; default is a rolled-back dry run",
    )
    args = parser.parse_args()
    with get_session_maker()() as session:
        changed = associate(session, args.organisation, args.subscription)
        if args.apply:
            session.commit()
        else:
            session.rollback()
        print(
            f"{'Applied' if args.apply else 'Dry run'}: {'new association' if changed else 'already associated'}"
        )


if __name__ == "__main__":
    main()
