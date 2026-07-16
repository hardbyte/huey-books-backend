"""Integration tests for adding/listing school staff during onboarding."""

from uuid import uuid4


def test_add_and_list_school_staff(
    client, test_wrivetedadmin_account_headers, session, test_school
):
    email = f"teacher-{uuid4().hex[:10]}@example.com"
    resp = client.post(
        f"/v1/school/{test_school.wriveted_identifier}/staff",
        headers=test_wrivetedadmin_account_headers,
        json={"name": "New Teacher", "email": email, "as_admin": False},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["email"] == email

    listed = client.get(
        f"/v1/school/{test_school.wriveted_identifier}/staff",
        headers=test_wrivetedadmin_account_headers,
    )
    assert listed.status_code == 200
    assert email in [m["email"] for m in listed.json()]


def test_add_staff_rejects_existing_email(
    client, test_wrivetedadmin_account_headers, session, test_school
):
    email = f"teacher-{uuid4().hex[:10]}@example.com"
    first = client.post(
        f"/v1/school/{test_school.wriveted_identifier}/staff",
        headers=test_wrivetedadmin_account_headers,
        json={"name": "First", "email": email},
    )
    assert first.status_code == 201, first.text
    # Same email again -> conflict (already has an account).
    again = client.post(
        f"/v1/school/{test_school.wriveted_identifier}/staff",
        headers=test_wrivetedadmin_account_headers,
        json={"name": "Dup", "email": email},
    )
    assert again.status_code == 409


def test_add_staff_requires_permission(client, session, test_school):
    resp = client.post(
        f"/v1/school/{test_school.wriveted_identifier}/staff",
        json={"name": "X", "email": "x@example.com"},
    )
    assert resp.status_code in (401, 403)
