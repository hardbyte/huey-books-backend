from uuid import uuid4


def test_unknown_requested_school_does_not_return_global_queue(
    client, backend_service_account_headers
):
    response = client.get(
        f"/v1/review-queue?school_id={uuid4()}",
        headers=backend_service_account_headers,
    )
    assert response.status_code == 404, response.text


def test_staff_can_still_request_global_queue(client, backend_service_account_headers):
    response = client.get("/v1/review-queue", headers=backend_service_account_headers)
    assert response.status_code == 200, response.text
