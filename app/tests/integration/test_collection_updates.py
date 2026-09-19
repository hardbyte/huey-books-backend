import csv
import random
import time

from app import crud
from app.services.editions import check_digit_13


def test_collection_timestamps(
    client,
    test_unhydrated_editions,
    test_user_account,
    test_user_account_headers,
):
    test_user_id = str(test_user_account.id)

    print("Creating new user collection")
    user_collection_create_response = client.post(
        "/v1/collection",
        headers=test_user_account_headers,
        json={
            "name": "Test collection",
            "user_id": test_user_id,
        },
    )
    user_collection_create_response.raise_for_status()
    user_collection_create_response_data = user_collection_create_response.json()
    user_collection_id = user_collection_create_response_data["id"]

    user_get_collection_response = client.get(
        f"/v1/collection/{user_collection_id}",
        headers=test_user_account_headers,
    )
    user_get_collection_response.raise_for_status()

    user_collection = user_get_collection_response.json()
    creation_time = user_collection["updated_at"]
    assert creation_time is not None

    print("Updating user collection")
    items = [
        {"edition_isbn": edition.isbn, "copies_total": 1, "copies_available": 1}
        for edition in test_unhydrated_editions
    ]
    user_collection_update_response = client.put(
        f"/v1/collection/{user_collection_id}/items",
        headers=test_user_account_headers,
        json=items,
    )
    user_collection_update_response.raise_for_status()

    user_get_collection_response = client.get(
        f"/v1/collection/{user_collection_id}",
        headers=test_user_account_headers,
    )
    user_get_collection_response.raise_for_status()

    user_collection = user_get_collection_response.json()
    assert user_collection["book_count"] == len(test_unhydrated_editions)
    assert user_collection["updated_at"] != creation_time


def test_collection_patch_update_ignores_new_books(
    client,
    test_unhydrated_editions,
    test_user_account,
    test_user_account_headers,
):
    test_user_id = str(test_user_account.id)

    print("Creating new user collection")
    user_collection_create_response = client.post(
        "/v1/collection",
        headers=test_user_account_headers,
        json={
            "name": "Test collection",
            "user_id": test_user_id,
        },
    )
    user_collection_create_response.raise_for_status()
    user_collection_create_response_data = user_collection_create_response.json()
    user_collection_id = user_collection_create_response_data["id"]

    user_get_collection_response = client.get(
        f"/v1/collection/{user_collection_id}",
        headers=test_user_account_headers,
    )
    user_get_collection_response.raise_for_status()

    user_collection = user_get_collection_response.json()
    creation_time = user_collection["updated_at"]
    assert creation_time is not None

    items = [
        {
            "action": "update",
            "edition_isbn": edition.isbn,
            "copies_total": 1,
            "copies_available": 1,
        }
        for edition in test_unhydrated_editions
    ]
    # Set the first action to "add" to check we still support both at once
    items[0]["action"] = "add"
    user_collection_update_response = client.patch(
        f"/v1/collection/{user_collection_id}",
        headers=test_user_account_headers,
        json={"items": items},
    )
    user_collection_update_response.raise_for_status()

    user_get_collection_response = client.get(
        f"/v1/collection/{user_collection_id}",
        headers=test_user_account_headers,
    )
    user_get_collection_response.raise_for_status()

    user_collection = user_get_collection_response.json()
    assert user_collection["book_count"] == 1
    assert user_collection["updated_at"] != creation_time


def test_collection_management(
    client,
    session,
    test_school,
    test_user_account,
    lms_service_account_headers_for_school,
    test_user_account_headers,
    test_data_path,
):
    """
    Test out the collection mechanisms of the Wriveted API.

    """
    start_time = time.time()
    print("Checking authorization")
    school_lms_account_details_response = client.get(
        "/v1/auth/me", headers=lms_service_account_headers_for_school
    )
    school_lms_account_details_response.raise_for_status()
    user_account_details_response = client.get(
        "/v1/auth/me", headers=test_user_account_headers
    )
    user_account_details_response.raise_for_status()

    INITIAL_NUMBER_OF_HYDRATED_BOOKS = 10
    INITIAL_NUMBER_OF_UNHYDRATED_BOOKS = 10
    NUMBER_INVALID_ISBNS = 10
    UPDATED_NUMBER_OF_BOOKS = 10
    ADDED_NUMBER_OF_BOOKS = 10
    REMOVED_NUMBER_OF_BOOKS = 10

    test_school_id = str(test_school.wriveted_identifier)
    test_user_id = str(test_user_account.id)

    # ----------------- RESET COLLECTION(s) -----------------

    # school

    print("Resetting school collection")
    school_collection_id = test_school.collection.id if test_school.collection else None
    if school_collection_id:
        school_collection_reset_response = client.delete(
            f"/v1/collection/{school_collection_id}",
            headers=lms_service_account_headers_for_school,
        )
        school_collection_reset_response.raise_for_status()

    # user

    print("Resetting user collection")
    user_collection_id = (
        test_user_account.collection.id if test_user_account.collection else None
    )
    if user_collection_id:
        user_collection_reset_response = client.delete(
            f"/v1/collection/{user_collection_id}",
            headers=test_user_account_headers,
        )
        user_collection_reset_response.raise_for_status()

    # ----------------- CREATE COLLECTION(s) -----------------

    # school

    print("Creating new school collection")
    school_collection_create_response = client.post(
        "/v1/collection",
        headers=lms_service_account_headers_for_school,
        json={
            "name": "Test collection",
            "school_id": test_school_id,
        },
    )
    school_collection_create_response.raise_for_status()
    school_collection_create_response_data = school_collection_create_response.json()
    school_collection_id = school_collection_create_response_data["id"]

    school_get_collection_response = client.get(
        f"/v1/collection/{school_collection_id}",
        headers=lms_service_account_headers_for_school,
    )
    school_get_collection_response.raise_for_status()

    school_collection = school_get_collection_response.json()
    assert school_collection["book_count"] == 0
    print("Collection after creating blank:", school_collection)

    # user

    print("Creating new user collection")
    user_collection_create_response = client.post(
        "/v1/collection",
        headers=test_user_account_headers,
        json={
            "name": "Test collection",
            "user_id": test_user_id,
        },
    )
    user_collection_create_response.raise_for_status()
    user_collection_create_response_data = user_collection_create_response.json()
    user_collection_id = user_collection_create_response_data["id"]

    user_get_collection_response = client.get(
        f"/v1/collection/{user_collection_id}",
        headers=test_user_account_headers,
    )
    user_get_collection_response.raise_for_status()

    user_collection = user_get_collection_response.json()
    assert user_collection["book_count"] == 0
    print("Collection after creating blank:", user_collection)

    # ----------------- PROCESS BOOKS -----------------

    print("Loading books from CSV file")

    book_data = []

    test_file_path = test_data_path / "test-books.csv"
    with open(test_file_path, newline="", encoding="utf-8") as csv_file:
        reader = csv.reader(csv_file)

        # Eat the header line
        next(reader)

        # The Airtable data has some duplicates
        seen_isbns = set()
        for i, book_row in enumerate(reader):
            if i > 2000:
                break
            authors = []
            if len(book_row[1]) > 1:
                authors.append(
                    {
                        "first_name": book_row[1].split()[0],
                        "last_name": book_row[1].split()[-1],
                    }
                )

            cover_url = None
            if len(book_row[17]) > 1 and book_row[17].startswith("http"):
                cover_url = book_row[17]

            for isbn in book_row[28].split(","):
                isbn = isbn.strip().upper()
                if isbn not in seen_isbns and len(isbn) > 0:
                    new_edition_data = {
                        # "work_title": "string",
                        "title": book_row[0].strip(),
                        "isbn": isbn.strip(),
                        "cover_url": cover_url,
                        "info": {
                            # "genre": book_row[20],
                            "pages": 50,
                            "other": {"airtable_dump": "|".join(book_row)},
                        },
                        "work_info": {
                            # "short_summary": short_summary,
                            # "long_summary": long_summary,
                            # "keywords": keywords,
                            # "interest_age": interest_age,
                            # "reading_age": reading_age,
                            # "genres": genres
                        },
                        "authors": authors,
                        "illustrators": [
                            # {
                            #     "full_name": "string",
                            #     "info": "string"
                            # }
                        ],
                    }
                    if book_row[80] is not None and len(book_row[80]) > 1:
                        # Add the series title
                        try:
                            (series_title, *_) = book_row[80].split(";")
                            new_edition_data["series_title"] = series_title.strip()
                        except ValueError:
                            print("Not adding this series - row was ", book_row[80])

                    book_data.append(new_edition_data)

                seen_isbns.add(isbn)

    assert len(book_data) > (INITIAL_NUMBER_OF_HYDRATED_BOOKS + ADDED_NUMBER_OF_BOOKS)

    original_hydrated = book_data[:INITIAL_NUMBER_OF_HYDRATED_BOOKS]
    assert len(original_hydrated) == INITIAL_NUMBER_OF_HYDRATED_BOOKS

    # create INITIAL_NUMBER_OF_UNHYDRATED_BOOKS valid ISBN13s
    valid_isbns = [
        str(valid_isbn_body) + check_digit_13(str(valid_isbn_body))
        for valid_isbn_body in random.sample(
            range(978000000000, 979000000000), INITIAL_NUMBER_OF_UNHYDRATED_BOOKS
        )
    ]
    original_unhydrated = [{"isbn": i} for i in valid_isbns]
    assert len(original_unhydrated) == INITIAL_NUMBER_OF_UNHYDRATED_BOOKS

    # create NUMBER_INVALID_ISBNS invalid isbns to include (apply an off-by-one on the valid check digit)
    invalid_isbns = [
        str(invalid_isbn_body)
        + str((int(check_digit_13(str(invalid_isbn_body))) + 1) % 10)
        for invalid_isbn_body in random.sample(
            range(978000000000, 979000000000), NUMBER_INVALID_ISBNS
        )
    ]
    invalid_books = [{"isbn": i} for i in invalid_isbns]

    original_books = original_hydrated + original_unhydrated + invalid_books
    assert (
        len(original_books)
        == INITIAL_NUMBER_OF_HYDRATED_BOOKS
        + INITIAL_NUMBER_OF_UNHYDRATED_BOOKS
        + NUMBER_INVALID_ISBNS
    )

    print(
        f"Adding the initial {len(original_hydrated)} hydrated books to db as Editions, Works, Authors etc."
    )
    add_books_response = client.post(
        "/v1/editions",
        json=original_hydrated,
        timeout=30,
        headers=lms_service_account_headers_for_school,
    )
    add_books_response.raise_for_status()
    print(add_books_response.json())

    def randomize_loan_status(book_data):
        data = book_data.copy()
        data["copies_total"] = random.randint(2, 4)
        data["copies_available"] = random.randint(0, 2)
        return data

    original_collection = [
        randomize_loan_status(
            {
                "edition_isbn": b["isbn"],
            }
        )
        for b in original_books
    ]

    print(
        f"{len(book_data)} Books loaded. Setting the collection(s) to the first {len(original_hydrated)} unhydrated books + {len(original_unhydrated)} hydrated books"
    )

    # ----------------- SET COLLECTION(s) -----------------

    # school

    print(
        f"Setting new collection of {len(original_collection)} hydrated + unhydrated books"
    )
    set_collection_response = client.put(
        f"/v1/collection/{school_collection_id}/items",
        json=original_collection,
        timeout=30,
        headers=lms_service_account_headers_for_school,
    )
    set_collection_response.raise_for_status()
    print(set_collection_response.json())

    print("Checking the collection items")
    get_collection_response = client.get(
        f"/v1/collection/{school_collection_id}/items",
        headers=lms_service_account_headers_for_school,
        params={"skip": 0, "limit": 2000},
        timeout=30,
    )

    get_collection_response.raise_for_status()
    collection_items = get_collection_response.json()

    assert "pagination" in collection_items

    for item in collection_items["data"]:
        assert item["copies_total"] > 1
    print("Collection after adding (first 3):\n", collection_items["data"][:3])
    # check that the number of books exactly matches the number of -valid- isbns provided
    assert (
        collection_items["pagination"]["total"]
        == INITIAL_NUMBER_OF_HYDRATED_BOOKS + INITIAL_NUMBER_OF_UNHYDRATED_BOOKS
    )
    assert (
        len(collection_items["data"])
        == INITIAL_NUMBER_OF_HYDRATED_BOOKS + INITIAL_NUMBER_OF_UNHYDRATED_BOOKS
    ), (
        f"Expected the collection to contain {INITIAL_NUMBER_OF_HYDRATED_BOOKS} items, but it had {len(collection_items)}"
    )

    # check collection search

    print("Checking the collection items can be filtered by title")

    get_collection_response = client.get(
        f"/v1/collection/{school_collection_id}/items",
        headers=lms_service_account_headers_for_school,
        params={"skip": 0, "limit": 2000, "title": "The"},
        timeout=30,
    )
    get_collection_response.raise_for_status()
    collection_items = get_collection_response.json()
    assert "pagination" in collection_items
    assert collection_items["pagination"]["total"] < len(original_collection)

    # user

    print(
        f"Setting new collection of {len(original_collection)} hydrated + unhydrated books"
    )
    set_collection_response = client.put(
        f"/v1/collection/{user_collection_id}/items",
        json=original_collection,
        timeout=30,
        headers=test_user_account_headers,
    )
    set_collection_response.raise_for_status()
    print(set_collection_response.json())

    print("Checking the collection items")
    get_collection_response = client.get(
        f"/v1/collection/{user_collection_id}/items",
        headers=test_user_account_headers,
        params={"skip": 0, "limit": 2000},
        timeout=30,
    )

    get_collection_response.raise_for_status()
    collection_items = get_collection_response.json()
    for item in collection_items["data"]:
        assert item["copies_total"] > 1
    print("Collection after adding (first 3):\n", collection_items["data"][:3])
    # check that the number of books exactly matches the number of -valid- isbns provided
    assert (
        len(collection_items["data"])
        == INITIAL_NUMBER_OF_HYDRATED_BOOKS + INITIAL_NUMBER_OF_UNHYDRATED_BOOKS
    ), (
        f"Expected the collection to contain {INITIAL_NUMBER_OF_HYDRATED_BOOKS} items, but it had {len(collection_items)}"
    )

    # ----------------- UPDATE COLLECTION LOAN STATUSES -----------------

    # Update the collection by changing the loan status of a subset of the books.
    books_to_update = original_collection[:UPDATED_NUMBER_OF_BOOKS]
    print("Bulk updating loan status via `PATCH .../collection/{id}/items` API")
    time.sleep(0.1)
    collection_changes = [
        {
            "action": "update",
            "edition_isbn": b["edition_isbn"],
            "copies_total": 99,
            "copies_available": 99,
        }
        for b in books_to_update
    ]

    # school

    print(f"Sending through {len(collection_changes)} updates")
    updates_response = client.patch(
        f"/v1/collection/{school_collection_id}",
        json={"items": collection_changes},
        timeout=120,
        headers=lms_service_account_headers_for_school,
    )
    updates_response.raise_for_status()
    print(updates_response.status_code)
    print(updates_response.json())
    print("Updated loan status")

    get_collection_response = client.get(
        f"/v1/collection/{school_collection_id}/items",
        headers=lms_service_account_headers_for_school,
        params={"skip": 0, "limit": 2000},
        timeout=120,
    )
    get_collection_response.raise_for_status()
    collection_items = get_collection_response.json()

    number_items_with_updated_loan_status = 0
    for item in collection_items["data"]:
        if item["copies_total"] == 99 and item["copies_available"] == 99:
            number_items_with_updated_loan_status += 1

    assert number_items_with_updated_loan_status == UPDATED_NUMBER_OF_BOOKS, (
        f"Expected {UPDATED_NUMBER_OF_BOOKS} to have different loan statuses - but found {number_items_with_updated_loan_status}"
    )
    print("Collection loan status has changed")

    # user

    print(f"Sending through {len(collection_changes)} updates")
    updates_response = client.patch(
        f"/v1/collection/{user_collection_id}",
        json={"items": collection_changes},
        timeout=120,
        headers=test_user_account_headers,
    )
    updates_response.raise_for_status()
    print(updates_response.status_code)
    print(updates_response.json())
    print("Updated loan status")

    get_collection_response = client.get(
        f"/v1/collection/{user_collection_id}/items",
        headers=test_user_account_headers,
        params={"skip": 0, "limit": 2000},
        timeout=120,
    )
    get_collection_response.raise_for_status()
    collection_items = get_collection_response.json()

    number_items_with_updated_loan_status = 0
    for item in collection_items["data"]:
        if item["copies_total"] == 99 and item["copies_available"] == 99:
            number_items_with_updated_loan_status += 1

    assert number_items_with_updated_loan_status == UPDATED_NUMBER_OF_BOOKS, (
        f"Expected {UPDATED_NUMBER_OF_BOOKS} to have different loan statuses - but found {number_items_with_updated_loan_status}"
    )
    print("Collection loan status has changed")

    # ----------------- ADD AND REMOVE FROM COLLECTION -----------------

    books_to_remove = original_collection[:REMOVED_NUMBER_OF_BOOKS]
    print("Adding and removing books from collection via `PATCH .../collection` API")
    collection_changes = [
        {
            "action": "remove",
            "edition_isbn": b["edition_isbn"],
        }
        for b in books_to_remove
    ]

    books_to_add = book_data[
        INITIAL_NUMBER_OF_HYDRATED_BOOKS : INITIAL_NUMBER_OF_HYDRATED_BOOKS
        + ADDED_NUMBER_OF_BOOKS
    ]
    collection_changes.extend(
        [
            randomize_loan_status(
                {
                    "action": "add",
                    "edition_isbn": b["isbn"],
                    "edition_info": b,
                }
            )
            for b in books_to_add
        ]
    )

    # school

    updates_response = client.patch(
        f"/v1/collection/{school_collection_id}",
        json={"items": collection_changes},
        timeout=120,
        headers=lms_service_account_headers_for_school,
    )
    updates_response.raise_for_status()
    print(updates_response.json())

    print("Added and removed books from collection")
    get_collection_response = client.get(
        f"/v1/collection/{school_collection_id}",
        headers=lms_service_account_headers_for_school,
        params={"skip": 0, "limit": 2000},
        timeout=120,
    )
    get_collection_response.raise_for_status()
    new_school_collection = get_collection_response.json()
    print(
        "Current collection size:",
        new_school_collection["book_count"],
        "expected: ",
        INITIAL_NUMBER_OF_HYDRATED_BOOKS
        + INITIAL_NUMBER_OF_UNHYDRATED_BOOKS
        + ADDED_NUMBER_OF_BOOKS
        - REMOVED_NUMBER_OF_BOOKS,
    )

    current_school_total = new_school_collection["book_count"]
    assert (
        current_school_total
        == INITIAL_NUMBER_OF_HYDRATED_BOOKS
        + INITIAL_NUMBER_OF_UNHYDRATED_BOOKS
        + ADDED_NUMBER_OF_BOOKS
        - REMOVED_NUMBER_OF_BOOKS
    )

    # user

    updates_response = client.patch(
        f"/v1/collection/{user_collection_id}",
        json={"items": collection_changes},
        timeout=120,
        headers=test_user_account_headers,
    )
    updates_response.raise_for_status()
    print(updates_response.json())

    print("Added and removed books from collection")
    get_collection_response = client.get(
        f"/v1/collection/{user_collection_id}",
        headers=test_user_account_headers,
        params={"skip": 0, "limit": 2000},
        timeout=120,
    )
    get_collection_response.raise_for_status()
    new_user_collection = get_collection_response.json()
    print(
        "Current collection size:",
        new_user_collection["book_count"],
        "expected: ",
        INITIAL_NUMBER_OF_HYDRATED_BOOKS
        + INITIAL_NUMBER_OF_UNHYDRATED_BOOKS
        + ADDED_NUMBER_OF_BOOKS
        - REMOVED_NUMBER_OF_BOOKS,
    )

    current_user_total = new_user_collection["book_count"]
    assert (
        current_user_total
        == INITIAL_NUMBER_OF_HYDRATED_BOOKS
        + INITIAL_NUMBER_OF_UNHYDRATED_BOOKS
        + ADDED_NUMBER_OF_BOOKS
        - REMOVED_NUMBER_OF_BOOKS
    )

    # ------------------- WITHOUT ISBNS -------------------

    NUMBER_WITHOUT_ISBNS = 10

    print("Adding 10 books without ISBNs to collection via `PATCH .../collection` API")
    collection_changes = [
        {
            "action": "add",
            "info": {
                "title": f"Test Book {i}",
                "author": f"Test Author {i}",
            },
        }
        for i in range(NUMBER_WITHOUT_ISBNS)
    ]

    # school

    updates_response = client.patch(
        f"/v1/collection/{school_collection_id}",
        json={"items": collection_changes},
        timeout=120,
        headers=lms_service_account_headers_for_school,
    )
    updates_response.raise_for_status()
    print(updates_response.json())

    print("Added books without ISBNs to school collection")
    get_collection_response = client.get(
        f"/v1/collection/{school_collection_id}",
        headers=lms_service_account_headers_for_school,
        params={"skip": 0, "limit": 2000},
        timeout=120,
    )
    get_collection_response.raise_for_status()
    new_school_collection = get_collection_response.json()
    print(
        "Current collection size:",
        new_school_collection["book_count"],
        "expected: ",
        current_school_total + NUMBER_WITHOUT_ISBNS,
    )
    assert (
        new_school_collection["book_count"]
        == current_school_total + NUMBER_WITHOUT_ISBNS
    )

    # user

    updates_response = client.patch(
        f"/v1/collection/{user_collection_id}",
        json={"items": collection_changes},
        timeout=120,
        headers=test_user_account_headers,
    )
    updates_response.raise_for_status()
    print(updates_response.json())

    print("Added books without ISBNs to user collection")
    get_collection_response = client.get(
        f"/v1/collection/{user_collection_id}",
        headers=test_user_account_headers,
        params={"skip": 0, "limit": 2000},
        timeout=120,
    )
    get_collection_response.raise_for_status()
    new_user_collection = get_collection_response.json()
    print(
        "Current collection size:",
        new_user_collection["book_count"],
        "expected: ",
        current_user_total + NUMBER_WITHOUT_ISBNS,
    )
    assert (
        new_user_collection["book_count"] == current_user_total + NUMBER_WITHOUT_ISBNS
    )

    # ----------------- ADDING INDIVIDUAL BOOK ------------

    print("Adding individual book to collection via `POST .../collection/id/item` API")

    # school

    add_book_response = client.post(
        f"/v1/collection/{school_collection_id}/item",
        json={
            "info": {
                "title": "Lonely Book",
                "author": "Lonely Author",
            },
        },
        timeout=120,
        headers=lms_service_account_headers_for_school,
    )
    add_book_response.raise_for_status()
    print(add_book_response.json())

    print("Added book to school collection")
    get_collection_response = client.get(
        f"/v1/collection/{school_collection_id}",
        headers=lms_service_account_headers_for_school,
        params={"skip": 0, "limit": 2000},
        timeout=120,
    )
    get_collection_response.raise_for_status()
    new_school_collection = get_collection_response.json()
    print(
        "Current collection size:",
        new_school_collection["book_count"],
        "expected: ",
        current_school_total + NUMBER_WITHOUT_ISBNS + 1,
    )
    assert (
        new_school_collection["book_count"]
        == current_school_total + NUMBER_WITHOUT_ISBNS + 1
    )

    # user

    add_book_response = client.post(
        f"/v1/collection/{user_collection_id}/item",
        json={
            "info": {
                "title": "Lonely Book",
                "author": "Lonely Author",
            },
        },
        timeout=120,
        headers=test_user_account_headers,
    )
    add_book_response.raise_for_status()
    print(add_book_response.json())

    print("Added book to user collection")
    get_collection_response = client.get(
        f"/v1/collection/{user_collection_id}",
        headers=test_user_account_headers,
        params={"skip": 0, "limit": 2000},
        timeout=120,
    )
    get_collection_response.raise_for_status()
    new_user_collection = get_collection_response.json()
    print(
        "Current collection size:",
        new_user_collection["book_count"],
        "expected: ",
        current_user_total + NUMBER_WITHOUT_ISBNS + 1,
    )
    assert (
        new_user_collection["book_count"]
        == current_user_total + NUMBER_WITHOUT_ISBNS + 1
    )

    # ----------------- DELETE COLLECTION -----------------

    print("Deleting collection via `DELETE .../collection` API")

    # school

    remove_collection_response = client.delete(
        f"/v1/collection/{school_collection_id}",
        headers=lms_service_account_headers_for_school,
        timeout=120,
    )
    remove_collection_response.raise_for_status()
    print("Removed collection")
    assert (
        len(
            crud.collection.get_collection_items_by_collection_id(
                db=session, collection_id=school_collection["id"]
            )
        )
        == 0
    )
    print("And the deletion cascaded down to collection items")

    # user

    remove_collection_response = client.delete(
        f"/v1/collection/{user_collection_id}",
        headers=test_user_account_headers,
        timeout=120,
    )
    remove_collection_response.raise_for_status()
    print("Removed collection")
    assert (
        len(
            crud.collection.get_collection_items_by_collection_id(
                db=session, collection_id=user_collection["id"]
            )
        )
        == 0
    )
    print("And the deletion cascaded down to collection items")

    print(f"Processing took: {time.time() - start_time:.2f} seconds")
