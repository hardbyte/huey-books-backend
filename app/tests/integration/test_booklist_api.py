import secrets

from starlette import status

from app.models.booklist import ListSharingType, ListType


def test_backend_service_account_can_list_booklists_empty(
    client, backend_service_account_headers
):
    response = client.get("v1/lists", headers=backend_service_account_headers)

    assert response.status_code == status.HTTP_200_OK


def test_create_empty_booklist_invalid_data_returns_validation_error(
    client, backend_service_account_headers
):
    response = client.post(
        "v1/list",
        headers=backend_service_account_headers,
        json={"name": "my almost valid booklist", "type": "an invalid book list type"},
    )
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


def test_create_empty_booklist(client, test_user_account_headers):
    response = client.post(
        "v1/list",
        headers=test_user_account_headers,
        json={"name": "empty wishes", "type": ListType.PERSONAL},
    )

    assert response.status_code == status.HTTP_200_OK, response.text
    data = response.json()
    print(data)
    assert "id" in data

    detail_response = client.get(
        f"v1/list/{data['id']}", headers=test_user_account_headers
    )

    assert detail_response.status_code == status.HTTP_200_OK
    detail = detail_response.json()

    assert "name" in detail
    assert "empty wishes" == detail["name"]
    assert "id" in detail
    assert detail["id"] == data["id"]
    assert "book_count" in detail
    assert detail["book_count"] == 0
    assert "data" in detail
    works = detail["data"]
    assert len(works) == 0


def test_school_admin_can_create_school_booklist(
    client, admin_of_test_school_headers, works_list
):
    response = client.post(
        "v1/list",
        headers=admin_of_test_school_headers,
        json={
            "name": "wizard wishes",
            "type": ListType.SCHOOL,
            "items": [
                {"work_id": w.id, "order_id": i} for i, w in enumerate(works_list)
            ],
        },
    )

    assert response.status_code == status.HTTP_200_OK
    detail_response = client.get(
        f"v1/list/{response.json()['id']}",
        params={"limit": 10},
        headers=admin_of_test_school_headers,
    )

    assert detail_response.status_code == status.HTTP_200_OK
    detail = detail_response.json()
    assert "school" in detail
    assert detail["school"] is not None


def test_user_account_can_create_personal_booklist(
    client, test_user_account_headers, works_list
):
    response = client.post(
        "v1/list",
        headers=test_user_account_headers,
        json={
            "name": "wizard wishes",
            "type": ListType.PERSONAL,
            "items": [
                {"work_id": w.id, "order_id": i} for i, w in enumerate(works_list)
            ],
        },
    )

    assert response.status_code == status.HTTP_200_OK
    detail_response = client.get(
        f"v1/list/{response.json()['id']}",
        params={"limit": 10},
        headers=test_user_account_headers,
    )

    assert detail_response.status_code == status.HTTP_200_OK
    detail = detail_response.json()
    assert "user" in detail
    assert detail["user"] is not None


def test_create_booklist(client, backend_service_account_headers, works_list):
    response = client.post(
        "v1/list",
        headers=backend_service_account_headers,
        json={
            "name": "wizard wishes",
            "type": ListType.OTHER_LIST,
            "items": [
                {"work_id": w.id, "order_id": i} for i, w in enumerate(works_list)
            ],
        },
    )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert "id" in data
    assert "data" not in data

    detail_response = client.get(
        f"v1/list/{data['id']}",
        params={"limit": 10},
        headers=backend_service_account_headers,
    )

    assert detail_response.status_code == status.HTTP_200_OK
    detail = detail_response.json()

    assert "name" in detail
    assert "wizard wishes" == detail["name"]
    assert "id" in detail
    assert detail["book_count"] == 100
    assert detail["id"] == data["id"]
    assert "data" in detail
    items = detail["data"]
    assert len(items) == 10
    for item in items:
        assert "order_id" in item
        assert "work_id" in item
        assert "work" in item
        assert "title" in item["work"]


def test_anyone_can_create_personal_booklist(
    client, test_user_account_headers, works_list
):
    response = client.post(
        "v1/list",
        headers=test_user_account_headers,
        json={
            "name": "my absolute must reads",
            "type": ListType.PERSONAL,
            "items": [
                {"work_id": w.id, "order_id": i} for i, w in enumerate(works_list)
            ],
        },
    )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert "id" in data
    assert "data" not in data

    detail_response = client.get(
        f"v1/list/{data['id']}",
        params={"limit": 10},
        headers=test_user_account_headers,
    )

    assert detail_response.status_code == status.HTTP_200_OK
    detail = detail_response.json()

    assert "name" in detail
    assert detail["name"] == "my absolute must reads"
    assert "id" in detail
    assert detail["book_count"] == 100
    assert detail["id"] == data["id"]
    assert "data" in detail
    items = detail["data"]
    assert len(items) == 10
    for item in items:
        assert "order_id" in item
        assert "work_id" in item
        assert "work" in item
        assert "title" in item["work"]


def test_create_school_booklist_without_positions(
    client, admin_of_test_school_headers, works_list
):
    response = client.post(
        "v1/list",
        headers=admin_of_test_school_headers,
        json={
            "name": "wizard wishes",
            "type": ListType.SCHOOL,
            "items": [{"work_id": w.id} for w in works_list],
        },
    )

    assert response.status_code == status.HTTP_200_OK, response.text
    booklist_id = response.json()["id"]
    ensure_booklist_order_continuous(
        client, admin_of_test_school_headers, booklist_id=booklist_id
    )


def test_create_booklist_with_item_info(
    client, admin_of_test_school_headers, works_list
):
    response = client.post(
        "v1/list",
        headers=admin_of_test_school_headers,
        json={
            "name": "wizard wishes",
            "type": ListType.PERSONAL,
            "items": [
                {
                    "work_id": w.id,
                    "info": {"edition": w.editions[0].isbn, "note": "blah"},
                }
                for w in works_list
            ],
        },
    )
    print(response.text)
    assert response.status_code == status.HTTP_200_OK
    booklist_id = response.json()["id"]
    ensure_booklist_order_continuous(
        client, admin_of_test_school_headers, booklist_id=booklist_id
    )


def test_rename_personal_booklist(client, test_user_account_headers, works_list):
    create_booklist_response = client.post(
        "v1/list",
        headers=test_user_account_headers,
        json={
            "name": "wizard wishes",
            "type": ListType.PERSONAL,
            "items": [
                {"work_id": w.id, "order_id": i} for i, w in enumerate(works_list[:20])
            ],
        },
    )
    booklist_id = create_booklist_response.json()["id"]

    # Now patch it to change the name
    edit_booklist_response = client.patch(
        f"v1/list/{booklist_id}",
        headers=test_user_account_headers,
        json={"name": "witches wonders"},
    )
    assert edit_booklist_response.status_code == 200
    assert edit_booklist_response.json()["name"] == "witches wonders"


def test_user_cant_rename_huey_booklist(
    client, backend_service_account_headers, test_user_account_headers, works_list
):
    create_booklist_response = client.post(
        "v1/list",
        headers=backend_service_account_headers,
        json={
            "name": "witches wants",
            "type": ListType.HUEY,
            "items": [
                {"work_id": w.id, "order_id": i} for i, w in enumerate(works_list[:20])
            ],
        },
    )
    booklist_id = create_booklist_response.json()["id"]

    # Now if a normal user tries to patch it to change the name:
    edit_booklist_response = client.patch(
        f"v1/list/{booklist_id}",
        headers=test_user_account_headers,
        json={"name": "witches wonders"},
    )
    assert edit_booklist_response.status_code == 403


def test_user_cant_create_huey_booklist(client, test_user_account_headers, works_list):
    create_booklist_response = client.post(
        "v1/list",
        headers=test_user_account_headers,
        json={
            "name": "what does this button do",
            "type": ListType.HUEY,
            "items": [
                {"work_id": w.id, "order_id": i} for i, w in enumerate(works_list[:20])
            ],
        },
    )
    assert create_booklist_response.status_code == 403


def test_user_cant_create_region_booklist(
    client, test_user_account_headers, works_list
):
    create_booklist_response = client.post(
        "v1/list",
        headers=test_user_account_headers,
        json={
            "name": "what does this button do",
            "type": ListType.REGION,
            "items": [
                {"work_id": w.id, "order_id": i} for i, w in enumerate(works_list[:20])
            ],
        },
    )
    assert create_booklist_response.status_code == 403


def test_change_booklist_type(client, backend_service_account_headers, works_list):
    create_booklist_response = client.post(
        "v1/list",
        headers=backend_service_account_headers,
        json={
            "name": "wizard wishes",
            "type": ListType.OTHER_LIST,
            "info": {"description": "I'm the original"},
            "items": [
                {"work_id": w.id, "order_id": i} for i, w in enumerate(works_list[:20])
            ],
        },
    )
    assert create_booklist_response.json()["book_count"] == 20
    booklist_id = create_booklist_response.json()["id"]

    # Now patch it to change the type
    edit_booklist_response = client.patch(
        f"v1/list/{booklist_id}",
        headers=backend_service_account_headers,
        json={
            "type": ListType.REGION,
            # Note this will replace the info blob entirely:
            "info": {"description": "I'm the replacement"},
        },
    )
    assert edit_booklist_response.status_code == 200
    assert edit_booklist_response.json()["type"] == ListType.REGION

    detail_booklist_response = client.get(
        f"v1/list/{booklist_id}", headers=backend_service_account_headers
    )
    print(detail_booklist_response.json())
    assert detail_booklist_response.status_code == 200
    assert detail_booklist_response.json()["type"] == ListType.REGION
    assert len(detail_booklist_response.json()["data"]) >= 20
    assert "foo" not in detail_booklist_response.json()["info"]
    assert "replacement" in detail_booklist_response.json()["info"]["description"]


def test_add_items_to_booklist(client, backend_service_account_headers, works_list):
    create_booklist_response = client.post(
        "v1/list",
        headers=backend_service_account_headers,
        json={
            "name": "wizard wishes",
            "type": ListType.OTHER_LIST,
            "info": {"foo": 42},
            "items": [
                {"work_id": w.id, "order_id": i} for i, w in enumerate(works_list[:20])
            ],
        },
    )
    booklist_id = create_booklist_response.json()["id"]

    # Now patch it to add new items
    edit_booklist_response = client.patch(
        f"v1/list/{booklist_id}",
        headers=backend_service_account_headers,
        json={
            "items": [
                {"action": "add", "work_id": w.id, "order_id": i}
                for i, w in enumerate(works_list[20:], start=19)
            ]
        },
    )
    assert edit_booklist_response.status_code == 200
    assert edit_booklist_response.json()["book_count"] > 20
    ensure_booklist_order_continuous(
        client, backend_service_account_headers, booklist_id
    )


def test_add_items_without_position_to_booklist(
    client, backend_service_account_headers, works_list
):
    create_booklist_response = client.post(
        "v1/list",
        headers=backend_service_account_headers,
        json={
            "name": "wizard wishes",
            "type": ListType.OTHER_LIST,
            "info": {"foo": 42},
            "items": [{"work_id": w.id} for w in works_list[:20]],
        },
    )
    booklist_id = create_booklist_response.json()["id"]

    # Now patch it to add new items
    edit_booklist_response = client.patch(
        f"v1/list/{booklist_id}",
        headers=backend_service_account_headers,
        json={
            "items": [
                {"action": "add", "work_id": w.id, "order_id": i}
                for i, w in enumerate(works_list[20:], start=19)
            ]
        },
    )
    assert edit_booklist_response.status_code == 200
    assert edit_booklist_response.json()["book_count"] > 20
    ensure_booklist_order_continuous(
        client, backend_service_account_headers, booklist_id
    )


def test_add_existing_items_to_booklist(
    client, backend_service_account_headers, works_list
):
    create_booklist_response = client.post(
        "v1/list",
        headers=backend_service_account_headers,
        json={
            "name": "wizard wishes",
            "type": ListType.OTHER_LIST,
            "info": {"foo": 42},
            "items": [{"work_id": w.id} for w in works_list[:20]],
        },
    )
    booklist_id = create_booklist_response.json()["id"]

    # Now patch it to add some new and some existing items
    edit_booklist_response = client.patch(
        f"v1/list/{booklist_id}",
        headers=backend_service_account_headers,
        json={
            "items": [
                {"action": "add", "work_id": w.id}
                for i, w in enumerate(works_list[10:30])
            ]
        },
    )
    assert edit_booklist_response.status_code == 200
    assert edit_booklist_response.json()["book_count"] == 30


def test_remove_missing_items_from_booklist(
    client, backend_service_account_headers, works_list
):
    create_booklist_response = client.post(
        "v1/list",
        headers=backend_service_account_headers,
        json={
            "name": "wizard wishes",
            "type": ListType.OTHER_LIST,
            "info": {"foo": 42},
            "items": [
                {"work_id": w.id, "order_id": i} for i, w in enumerate(works_list[:10])
            ],
        },
    )
    booklist_id = create_booklist_response.json()["id"]

    # Now patch it to remove items that were never in the book list - should be ignored
    edit_booklist_response = client.patch(
        f"v1/list/{booklist_id}",
        headers=backend_service_account_headers,
        json={
            "items": [{"action": "remove", "work_id": w.id} for w in works_list[20:]]
        },
    )
    assert edit_booklist_response.status_code == 200
    assert edit_booklist_response.json()["book_count"] == 10
    ensure_booklist_order_continuous(
        client, backend_service_account_headers, booklist_id
    )


def test_admin_can_remove_items_from_personal_booklist(
    client, test_user_account, backend_service_account_headers, works_list
):
    create_booklist_response = client.post(
        "v1/list",
        headers=backend_service_account_headers,
        json={
            "name": "wizard wishes",
            "type": ListType.PERSONAL,
            "user_id": str(test_user_account.id),
            "info": {"foo": 42},
            "items": [
                {"work_id": w.id, "order_id": i} for i, w in enumerate(works_list[:20])
            ],
        },
    )
    create_booklist_response.raise_for_status()
    booklist_id = create_booklist_response.json()["id"]

    # Now patch it to remove items
    edit_booklist_response = client.patch(
        f"v1/list/{booklist_id}",
        headers=backend_service_account_headers,
        json={
            "items": [{"action": "remove", "work_id": w.id} for w in works_list[10:20]]
        },
    )
    assert edit_booklist_response.status_code == 200
    assert edit_booklist_response.json()["book_count"] == 10
    ensure_booklist_order_continuous(
        client, backend_service_account_headers, booklist_id
    )


def test_reorder_item_up_booklist(
    client, backend_service_account_headers, small_works_list
):
    create_booklist_response = client.post(
        "v1/list",
        headers=backend_service_account_headers,
        json={
            "name": "wizard wishes",
            "type": ListType.OTHER_LIST,
            "info": {"foo": 42},
            "items": [{"work_id": w.id} for w in small_works_list[:20]],
        },
    )
    booklist_id = create_booklist_response.json()["id"]

    # Now patch it to reorder a single item to the top of the list
    edit_booklist_response = client.patch(
        f"v1/list/{booklist_id}",
        headers=backend_service_account_headers,
        json={
            "items": [
                {"action": "update", "work_id": small_works_list[5].id, "order_id": 1}
            ]
        },
    )
    assert edit_booklist_response.status_code == 200

    ensure_booklist_order_continuous(
        client, backend_service_account_headers, booklist_id
    )


def test_reorder_item_down_booklist(
    client, backend_service_account_headers, small_works_list
):
    create_booklist_response = client.post(
        "v1/list",
        headers=backend_service_account_headers,
        json={
            "name": "wizard wishes",
            "type": ListType.OTHER_LIST,
            "info": {"foo": 42},
            "items": [{"work_id": w.id} for w in small_works_list[:20]],
        },
    )
    booklist_id = create_booklist_response.json()["id"]

    # Now patch it to reorder a few items
    edit_booklist_response = client.patch(
        f"v1/list/{booklist_id}",
        headers=backend_service_account_headers,
        json={
            "items": [
                {"action": "update", "work_id": small_works_list[5].id, "order_id": 10}
            ]
        },
    )
    assert edit_booklist_response.status_code == 200

    ensure_booklist_order_continuous(
        client, backend_service_account_headers, booklist_id
    )


def ensure_booklist_order_continuous(
    client, backend_service_account_headers, booklist_id
):
    # check order is continuous
    detail_booklist_response = client.get(
        f"v1/list/{booklist_id}", headers=backend_service_account_headers
    )
    detail = detail_booklist_response.json()
    positions = [item["order_id"] for item in detail["data"]]
    assert positions == list(range(len(positions)))


def test_enriched_booklist_response(
    client, backend_service_account_headers, works_list
):
    create_booklist_response = client.post(
        "v1/list",
        headers=backend_service_account_headers,
        json={
            "name": "wizard wishes enriched",
            "type": ListType.OTHER_LIST,
            "info": {"foo": 42},
            "items": [
                {
                    "work_id": w.id,
                    "order_id": i,
                    "info": {"edition": w.editions[0].isbn},
                }
                for i, w in enumerate(works_list[:20])
            ],
        },
    )
    assert create_booklist_response.status_code == 200
    booklist_id = create_booklist_response.json()["id"]

    # GET, default
    get_default_booklist_response = client.get(
        f"v1/list/{booklist_id}",
        headers=backend_service_account_headers,
    )
    assert get_default_booklist_response.status_code == 200
    json = get_default_booklist_response.json()
    assert isinstance(json["data"][0]["info"]["edition"], str)

    # GET, enriched
    get_enriched_booklist_response = client.get(
        f"v1/list/{booklist_id}?enriched=true",
        headers=backend_service_account_headers,
    )
    assert get_enriched_booklist_response.status_code == 200
    json = get_enriched_booklist_response.json()
    assert "isbn" in json["data"][0]["edition"]


def test_create_public_huey_booklist(
    client,
    backend_service_account_headers,
    test_user_account_headers,
    works_list,
):
    slug = f"wizard-wishes-{secrets.token_hex(8)}"

    # test that a public huey booklist can't be created with user rights
    create_booklist_response = client.post(
        "v1/list",
        headers=test_user_account_headers,
        json={
            "name": "wizard wishes",
            "type": ListType.HUEY,
            "slug": slug,
            "sharing": ListSharingType.PUBLIC,
            "info": {"foo": 42},
            "items": [
                {
                    "work_id": w.id,
                    "order_id": i,
                    "info": {"edition": w.editions[0].isbn},
                }
                for i, w in enumerate(works_list[:20])
            ],
        },
    )
    assert create_booklist_response.status_code == 403

    # test that a public huey booklist can be created with admin rights
    create_booklist_response = client.post(
        "v1/list",
        headers=backend_service_account_headers,
        json={
            "name": "wizard wishes",
            "type": ListType.HUEY,
            "slug": slug,
            "sharing": ListSharingType.PUBLIC,
            "info": {"foo": 42},
            "items": [
                {
                    "work_id": w.id,
                    "order_id": i,
                    "info": {"edition": w.editions[0].isbn},
                }
                for i, w in enumerate(works_list[:20])
            ],
        },
    )
    assert create_booklist_response.status_code == 200


def test_get_public_huey_booklist_without_auth(
    client, backend_service_account_headers, works_list
):
    slug = f"top-20-ultra-cool-books-{secrets.token_hex(8)}"

    # create a public huey booklist
    create_booklist_response = client.post(
        "v1/list",
        headers=backend_service_account_headers,
        json={
            "name": "top 20 ultra cool books",
            "type": ListType.HUEY,
            "slug": slug,
            "sharing": ListSharingType.PUBLIC,
            "info": {"foo": 42},
            "items": [
                {
                    "work_id": w.id,
                    "order_id": i,
                    "info": {"edition": w.editions[0].isbn},
                }
                for i, w in enumerate(works_list[:20])
            ],
        },
    )
    assert create_booklist_response.status_code == 200

    # get the public huey booklist without auth
    get_booklist_response = client.get(
        f"v1/public-list/{slug}",
    )
    assert get_booklist_response.status_code == 200
    json = get_booklist_response.json()
    assert json["name"] == "top 20 ultra cool books"
    assert json["type"] == ListType.HUEY
    assert json["slug"] == slug
    assert json["sharing"] == ListSharingType.PUBLIC
