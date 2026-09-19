import time

from starlette import status

from app import crud
from app.models import Edition


def test_backend_service_account_can_list_works(
    client, backend_service_account_headers
):
    response = client.get("v1/works", headers=backend_service_account_headers)
    assert response.status_code == status.HTTP_200_OK


def test_list_works_of_author(
    client, backend_service_account_headers, works_list, author_list
):
    response = client.get(
        "v1/works",
        params={"author_id": author_list[0].id},
        headers=backend_service_account_headers,
    )
    assert response.status_code == status.HTTP_200_OK


def test_backend_service_account_can_get_detail_on_specific_works(
    client, backend_service_account_headers, works_list
):
    for work in works_list[:3]:
        response = client.get(
            f"v1/work/{work.id}", headers=backend_service_account_headers
        )
        assert response.status_code == status.HTTP_200_OK
        work_data = response.json()
        assert work_data


def test_backend_service_account_can_get_edit_detail_on_specific_work(
    client, backend_service_account_headers, works_list
):
    work = works_list[0]
    response = client.patch(
        f"v1/work/{work.id}",
        json={"title": "New Title"},
        headers=backend_service_account_headers,
    )
    work_data = response.json()
    assert work_data["title"] == "New Title"


def test_backend_service_account_can_create_empty_work(
    client, backend_service_account_headers, works_list
):
    response = client.post(
        "v1/work",
        json={"title": "Test Work", "authors": [], "editions": []},
        headers=backend_service_account_headers,
    )
    work_data = response.json()
    assert "id" in work_data
    assert work_data["title"] == "Test Work"


def test_backend_service_account_can_delete_a_work(
    client, backend_service_account_headers, works_list
):
    response = client.delete(
        f"v1/work/{works_list[0].id}",
        headers=backend_service_account_headers,
    )
    work_data = response.json()
    assert "id" in work_data


def test_backend_service_account_can_label_work(
    client,
    backend_service_account,
    backend_service_account_headers,
    works_list,
    session_factory,
):
    work = works_list[0]
    response = client.patch(
        f"v1/work/{work.id}",
        json={
            "labelset": {
                "huey_summary": "Blarg!",
                "summary_origin": "HUMAN",
            }
        },
        headers=backend_service_account_headers,
    )
    work_data = response.json()
    assert work_data["labelset"]["huey_summary"] == "Blarg!"

    # Wait a tick, then see if an event was created
    time.sleep(0.01)
    with session_factory() as session:
        events = crud.event.get_all_with_optional_filters(
            db=session,
            service_account=backend_service_account,
            level="normal",
            query_string="Work updated",
            info_jsonpath_match=f"$.work_id=={work.id}",
        )

        assert len(events) == 1
        event = events[0]
        labelset_changes = event.info.get("changes").get("labelset")
        assert labelset_changes.get("huey_summary") == [None, "Blarg!"]
        assert labelset_changes.get("summary_origin") == [None, "HUMAN"]


def test_public_account_not_allowed_to_edit_work(
    client, test_user_account_headers, works_list
):
    work = works_list[0]
    response = client.patch(
        f"v1/work/{work.id}",
        json={"title": "New Title"},
        headers=test_user_account_headers,
    )
    assert response.status_code == 403


def test_student_account_not_allowed_to_edit_work(
    client, test_student_user_account_headers, works_list
):
    work = works_list[0]
    response = client.patch(
        f"v1/work/{work.id}",
        json={"title": "New Title"},
        headers=test_student_user_account_headers,
    )
    assert response.status_code == 403


def test_move_edition_to_new_work(client, backend_service_account_headers, works_list):
    original_work = works_list[0]
    test_edition: Edition = original_work.editions[0]

    response = client.post(
        "v1/work",
        json={
            "title": "New Test Work",
            "authors": [
                {
                    "first_name": original_work.authors[0].first_name,
                    "last_name": original_work.authors[0].last_name,
                },
                # Or by Author ID:
                # original_work.authors[0].id
            ],
            "editions": [test_edition.isbn],
        },
        headers=backend_service_account_headers,
    )
    response.raise_for_status()
    work_data = response.json()
    assert "id" in work_data
    assert work_data["title"] == "New Test Work"

    response = client.get(
        f"v1/edition/{test_edition.isbn}",
        headers=backend_service_account_headers,
    )
    edition_data = response.json()
    assert edition_data["work_id"] == work_data["id"]


def test_move_edition_to_new_work_with_existing_author(
    client, backend_service_account_headers, works_list
):
    original_work = works_list[0]
    test_edition: Edition = original_work.editions[0]

    response = client.post(
        "v1/work",
        json={
            "title": "New Test Work",
            "authors": [
                # {"first_name": original_work.authors[0].first_name, "last_name": original_work.authors[0].last_name},
                # Or by Author ID:
                original_work.authors[0].id
            ],
            "editions": [test_edition.isbn],
        },
        headers=backend_service_account_headers,
    )
    work_data = response.json()
    assert "id" in work_data
    assert work_data["title"] == "New Test Work"
    assert work_data["authors"][0]["id"] == str(original_work.authors[0].id), (
        "Author ID doesn't match"
    )

    response = client.get(
        f"v1/edition/{test_edition.isbn}",
        headers=backend_service_account_headers,
    )
    edition_data = response.json()
    assert edition_data["work_id"] == work_data["id"]


def test_move_edition_to_existing_work(
    client, backend_service_account_headers, works_list
):
    original_work = works_list[0]
    new_work = works_list[1]

    test_edition: Edition = original_work.editions[0]

    response = client.patch(
        f"v1/edition/{test_edition.isbn}",
        json={
            "work_id": new_work.id,
        },
        headers=backend_service_account_headers,
    )
    data = response.json()
    assert data["work_id"] == str(new_work.id), "work id doesn't match"

    # Confirm the edition has changed
    response = client.get(
        f"v1/edition/{test_edition.isbn}",
        headers=backend_service_account_headers,
    )
    edition_data = response.json()
    assert edition_data["work_id"] == str(new_work.id), (
        "Edition's work id hasn't updated"
    )
