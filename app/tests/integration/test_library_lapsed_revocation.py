from datetime import datetime, timedelta
from uuid import uuid4

from app.models import Educator
from app.models.subscription import Subscription
from app.tests.integration.test_organisation_workspace import workspace as workspace


async def test_lapsed_organisation_manager_can_revoke_but_not_grant(
    async_client,
    session,
    workspace,
    test_schooladmin_account,
    test_schooladmin_account_headers,
    test_wrivetedadmin_account_headers,
):
    organisation, libraries, _ = workspace
    staff = test_wrivetedadmin_account_headers
    manager = test_schooladmin_account_headers
    colleague = Educator(
        name="Delegated branch librarian",
        email=f"lapse-{uuid4()}@example.test",
        school_id=test_schooladmin_account.school_id,
        is_active=True,
    )
    session.add(colleague)
    session.commit()
    target_id = colleague.id
    members_path = f"/v1/libraries/{libraries[1]}/members"
    try:
        grant = await async_client.put(
            f"/v1/organisations/{organisation}/members/{test_schooladmin_account.id}",
            headers=staff,
            json={"role": "manager"},
        )
        assert grant.status_code == 200, grant.text
        grant = await async_client.put(
            f"{members_path}/{target_id}", headers=manager, json={"role": "manager"}
        )
        assert grant.status_code == 200, grant.text
        subscription = session.get(Subscription, f"sub_workspace_{organisation}")
        subscription.expiration = datetime.utcnow() - timedelta(seconds=1)
        session.commit()

        listed = await async_client.get(members_path, headers=manager)
        assert listed.status_code == 200, listed.text
        assert str(target_id) in [row["user_id"] for row in listed.json()["data"]]
        forbidden = await async_client.put(
            f"{members_path}/{target_id}", headers=manager, json={"role": "reviewer"}
        )
        assert forbidden.status_code == 403, forbidden.text
        revoked = await async_client.delete(
            f"{members_path}/{target_id}", headers=manager
        )
        assert revoked.status_code == 204, revoked.text
        forbidden = await async_client.put(
            f"{members_path}/{target_id}", headers=manager, json={"role": "manager"}
        )
        assert forbidden.status_code == 403, forbidden.text
        listed = await async_client.get(members_path, headers=manager)
        assert listed.json()["data"] == []
    finally:
        session.rollback()
        session.delete(colleague)
        session.commit()
