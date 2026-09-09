from unittest.mock import AsyncMock
from uuid import uuid4


def test_view_as_uses_real_school_permissions(
    client,
    test_wrivetedadmin_account_headers,
    test_schooladmin_account,
    test_school,
    monkeypatch,
):
    staff_headers = test_wrivetedadmin_account_headers
    start = client.post(
        f"/v1/auth/view-as/{test_schooladmin_account.id}", headers=staff_headers
    )
    assert start.status_code == 200, start.text
    headers = {**staff_headers, "X-View-As": start.json()["context"]}
    identity = client.get("/v1/auth/me", headers=headers)
    assert identity.status_code == 200, identity.text
    assert identity.json()["user"]["id"] == str(test_schooladmin_account.id)
    assert identity.json()["user"]["type"] == "school_admin"

    queue = AsyncMock(return_value=([], 0))
    monkeypatch.setattr("app.api.reviews.review_repository.get_review_queue", queue)
    response = client.get(f"/v1/review-queue?school_id={uuid4()}", headers=headers)
    assert response.status_code == 200, response.text
    assert queue.call_args.kwargs["school_id"] == test_school.id
    assert client.get("/v1/review-stats", headers=headers).status_code == 403
    assert (
        client.get(
            f"/v1/school/{test_school.wriveted_identifier}", headers=headers
        ).status_code
        == 200
    )
    assert client.patch("/v1/work/1", headers=headers, json={}).status_code == 403

    other = client.post(
        "/v1/school",
        headers=staff_headers,
        json={
            "name": "Other View as test school",
            "country_code": "ATA",
            "official_identifier": str(uuid4()),
            "info": {"location": {"state": "Required", "postcode": "Required"}},
        },
    )
    assert other.status_code == 200, other.text
    other_id = other.json()["wriveted_identifier"]
    try:
        assert client.get(f"/v1/school/{other_id}", headers=headers).status_code == 403
    finally:
        client.delete(f"/v1/school/{other_id}", headers=staff_headers)
