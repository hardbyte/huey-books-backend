from datetime import datetime

from app.models.user import UserAccountType
from app.repositories.review_access import library_review_evidence
from app.services.school_billing_status import select_paid_subscription


def can_review(session, account) -> bool:
    if not account.is_active:
        return False
    if account.type == UserAccountType.WRIVETED:
        return True
    if account.type not in (UserAccountType.EDUCATOR, UserAccountType.SCHOOL_ADMIN):
        return False
    if getattr(account, "school_id", None) is not None:
        return True
    direct, counts, subscriptions = library_review_evidence(session, account.id)
    return direct or any(
        count <= 1
        or select_paid_subscription(
            subscriptions.get(organisation_id, []), datetime.utcnow()
        )
        is not None
        for organisation_id, count in counts.items()
    )
