from uuid import UUID

from app.db.session import get_session_maker
from app.repositories.event_repository import event_repository
from app.services.account_refs import AccountRefType, load_account_ref


def record_booklist_collection_comparison_event(
    collection_id: str,
    booklist_id: str,
    items_in_common: int,
    account_type: AccountRefType | None = None,
    account_id: UUID | None = None,
):
    Session = get_session_maker()
    with Session() as session:
        account = load_account_ref(session, account_type, account_id)
        event_repository.create(
            session=session,
            title="Compared booklist and collection",
            info={
                "items_in_common": items_in_common,
                "collection_id": collection_id,
                "booklist_id": booklist_id,
            },
            account=account,
        )
