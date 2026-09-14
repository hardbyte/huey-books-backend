from sqlalchemy import func, select

from app.models.organisation import (
    LibraryMembership,
    OrganisationMembership,
    OrganisationSubscription,
)
from app.models.school import School
from app.models.subscription import Subscription


def library_review_evidence(session, user_id):
    direct = session.scalar(
        select(LibraryMembership.user_id)
        .where(
            LibraryMembership.user_id == user_id,
            LibraryMembership.role.in_(["manager", "reviewer"]),
        )
        .limit(1)
    )
    if direct is not None:
        return True, {}, {}
    managed = select(OrganisationMembership.organisation_id).where(
        OrganisationMembership.user_id == user_id
    )
    counts = dict(
        session.execute(
            select(School.organisation_id, func.count())
            .where(School.organisation_id.in_(managed))
            .group_by(School.organisation_id)
        ).all()
    )
    if not counts:
        return False, {}, {}
    subscriptions = {}
    for organisation_id, subscription in session.execute(
        select(OrganisationSubscription.organisation_id, Subscription)
        .join(Subscription, Subscription.id == OrganisationSubscription.subscription_id)
        .where(OrganisationSubscription.organisation_id.in_(counts))
    ):
        subscriptions.setdefault(organisation_id, []).append(subscription)
    return False, counts, subscriptions
