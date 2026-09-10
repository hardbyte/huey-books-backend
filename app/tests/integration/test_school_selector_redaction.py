import pytest


@pytest.mark.parametrize("subscribed", ["true", "false"])
@pytest.mark.parametrize("empty_info", [False, True])
def test_public_selector_ignores_private_filters_and_preserves_school(
    client,
    session,
    test_school,
    test_schooladmin_account,
    test_user_account_headers,
    backend_service_account_headers,
    subscribed,
    empty_info,
):
    if empty_info:
        test_school.info = None
        session.commit()
    params = {"official_identifier": test_school.official_identifier}
    before = client.get(
        "/v1/schools", params=params, headers=backend_service_account_headers
    )
    before.raise_for_status()
    assert len(before.json()) == 1
    assert before.json()[0]["state"] is not None
    assert before.json()[0]["admins"][0]["email"] == test_schooladmin_account.email

    response = client.get(
        "/v1/schools",
        params={
            **params,
            "has_active_subscription": subscribed,
            "is_active": "false",
            "connected_collection": "true",
        },
        headers=test_user_account_headers,
    )
    response.raise_for_status()
    assert len(response.json()) == 1
    school = response.json()[0]
    assert school["state"] is None
    assert school["subscription"] is None
    assert school["collection"] is None
    assert school["admins"] == [{}]

    session.expire_all()
    assert test_school.state.value == before.json()[0]["state"]
    after = client.get(
        "/v1/schools", params=params, headers=backend_service_account_headers
    )
    after.raise_for_status()
    assert after.json() == before.json()
    if empty_info:
        assert test_school.info is None
