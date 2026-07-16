from starlette import status

from app import crud
from app.api.dependencies.security import create_user_access_token
from app.models import Subscription
from app.repositories.subscription_repository import subscription_repository
from app.schemas.users.user_update import UserUpdateIn


def test_update_public_user_to_student_type(
    session, client, backend_service_account_headers, test_school, test_class_group
):
    email = "testemail@site.com"
    if user := crud.user.get_by_account_email(db=session, email=email):
        crud.user.remove(db=session, id=user.id)
    user = crud.user.create(
        db=session,
        obj_in=UserUpdateIn(
            name="testman123",
            email=email,
            first_name="test",
            last_name_initial="m",
        ),
    )

    response = client.get(f"v1/user/{user.id}", headers=backend_service_account_headers)
    assert response.status_code == status.HTTP_200_OK

    json = response.json()
    assert json["type"] == "public"

    # convert to student (but lacking class_id)
    user_type_update = {
        "type": "student",
        "username": "testuser123",
        "school_id": str(test_school.wriveted_identifier),
    }

    failed_update_response = client.patch(
        f"v1/user/{user.id}",
        headers=backend_service_account_headers,
        json=user_type_update,
    )

    assert failed_update_response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    # now add the class_id, completing the requirements to convert from public to student
    user_type_update["class_group_id"] = str(test_class_group.id)

    successful_update_response = client.patch(
        f"v1/user/{user.id}",
        headers=backend_service_account_headers,
        json=user_type_update,
    )

    assert successful_update_response.status_code == status.HTTP_200_OK
    json = successful_update_response.json()
    assert json["type"] == "student"

    session.delete(user)
    session.commit()


def test_update_student_to_public_reader(
    session,
    client,
    backend_service_account_headers,
    test_school,
    test_class_group,
    test_student_user_account,
):
    response = client.get(
        f"v1/user/{test_student_user_account.id}",
        headers=backend_service_account_headers,
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["type"] == "student"

    # convert to public
    user_type_update = {
        "type": "public",
    }

    successful_update_response = client.patch(
        f"v1/user/{test_student_user_account.id}",
        headers=backend_service_account_headers,
        json=user_type_update,
    )

    assert successful_update_response.status_code == status.HTTP_200_OK
    json = successful_update_response.json()
    assert json["type"] == "public"


def test_get_parent_user(
    session,
    client,
    backend_service_account_headers,
):
    email = "testemail@site.com"
    if user := crud.user.get_by_account_email(db=session, email=email):
        crud.user.remove(db=session, id=user.id)
    user = crud.user.create(
        db=session,
        obj_in=UserUpdateIn(name="A Parent", email=email, type="parent"),
    )

    response = client.get(f"v1/user/{user.id}", headers=backend_service_account_headers)
    assert response.status_code == status.HTTP_200_OK

    json = response.json()
    assert json["type"] == "parent"


def test_get_subscribed_parent_user(
    session, client, backend_service_account_headers, test_product
):
    email = "testemail@site.com"
    if user := crud.user.get_by_account_email(db=session, email=email):
        crud.user.remove(db=session, id=user.id)

    user = crud.user.create(
        db=session,
        obj_in=UserUpdateIn(name="Subscribed Parent", email=email, type="parent"),
    )

    subscription_id = "sub_123"
    if existing_subscription := subscription_repository.get_by_id(
        db=session, subscription_id=subscription_id
    ):
        subscription_repository.delete(
            db=session, subscription_id=existing_subscription.id
        )

    new_subscription = Subscription(
        id=subscription_id,
        parent_id=user.id,
        stripe_customer_id="cus_123",
        is_active=True,
        info={},
        product_id=test_product.id,
    )
    session.add(new_subscription)
    session.commit()

    # Test getting parent details includes subscription info
    response = client.get(f"v1/user/{user.id}", headers=backend_service_account_headers)
    assert response.status_code == status.HTTP_200_OK

    json = response.json()
    print(json)
    assert json["type"] == "parent"
    assert json["subscription"]["provider"] == "stripe"
    assert json["subscription"]["is_active"] is True
    assert json["subscription"]["type"] == "family"
    assert json["subscription"]["stripe_customer_id"] == "cus_123"
    assert json["subscription"]["id"] == "sub_123"

    # Test that querying users with active family subscriptions returns the
    # created parent
    response = client.get(
        "v1/users",
        params={"limit": 500, "active_subscription_type": "family"},
        headers=backend_service_account_headers,
    )
    assert response.status_code == status.HTTP_200_OK


def test_parent_user_can_login(
    session,
    client,
):
    email = "testemail@site.com"

    if user := crud.user.get_by_account_email(db=session, email=email):
        crud.user.remove(db=session, id=user.id)

    user = crud.user.create(
        db=session,
        obj_in=UserUpdateIn(name="A Parent", email=email, type="parent"),
    )

    parent_access_token = create_user_access_token(user)

    response = client.get(
        "v1/auth/me", headers={"Authorization": f"bearer {parent_access_token}"}
    )
    assert response.status_code == status.HTTP_200_OK

    json = response.json()
    assert json["account_type"] == "user"
    user_data = json["user"]
    assert user_data["id"] == str(user.id)
    assert user_data["type"] == "parent"
    assert user_data["is_active"]


def test_parent_create_via_api(session, client, backend_service_account_headers):
    email = "testemail@site.com"

    if existing_user := crud.user.get_by_account_email(db=session, email=email):
        crud.user.remove(db=session, id=existing_user.id)

    response = client.post(
        "v1/user",
        json={
            "name": "A Parent",
            "email": email,
            "type": "parent",
        },
        headers=backend_service_account_headers,
    )
    assert response.status_code == status.HTTP_200_OK
    user_id = response.json()["id"]
    response = client.post(
        f"v1/user/{user_id}/auth-token", headers=backend_service_account_headers
    )
    assert response.status_code == status.HTTP_200_OK
    parent_access_token = response.json()["access_token"]

    response = client.get(
        "v1/auth/me", headers={"Authorization": f"bearer {parent_access_token}"}
    )
    assert response.status_code == status.HTTP_200_OK

    json = response.json()
    assert json["account_type"] == "user"
    user_data = json["user"]
    assert user_data["id"] == str(user_id)
    assert user_data["type"] == "parent"
    assert user_data["is_active"]


def test_parent_create_with_child_via_api(
    session, client, backend_service_account_headers
):
    email = "testemail@site.com"

    if existing_user := crud.user.get_by_account_email(db=session, email=email):
        crud.user.remove(db=session, id=existing_user.id)

    response = client.post(
        "v1/user",
        json={
            "name": "Parent N. Child",
            "email": email,
            "type": "parent",
            "children": [
                {
                    "name": "Junior",
                    "type": "public",
                    "huey_attributes": {"age": "10", "reading_ability": ["SPOT"]},
                }
            ],
        },
        headers=backend_service_account_headers,
    )
    assert response.status_code == status.HTTP_200_OK
    json = response.json()
    assert json["children"][0]["name"] == "Junior"

    child_id = json["children"][0]["id"]
    child_response = client.get(
        f"v1/user/{child_id}", headers=backend_service_account_headers
    )

    assert child_response.status_code == status.HTTP_200_OK
    child_json = child_response.json()
    assert child_json["huey_attributes"]["age"] == 10
    assert child_json["huey_attributes"]["reading_ability"] == ["SPOT"]


def test_user_create_with_checkout_session_id(
    session, client, backend_service_account_headers, test_product
):
    email = "testemail@site.com"
    if existing_user := crud.user.get_by_account_email(db=session, email=email):
        crud.user.remove(db=session, id=existing_user.id)

    subscription_id = "sub_123"
    if existing_subscription := subscription_repository.get_by_id(
        db=session, subscription_id=subscription_id
    ):
        subscription_repository.delete(
            db=session, subscription_id=existing_subscription.id
        )

    orphaned_subscription = Subscription(
        id="sub_123",
        parent_id=None,
        stripe_customer_id="cus_123",
        is_active=True,
        info={},
        product_id=test_product.id,
        latest_checkout_session_id="TEST_CHECKOUT_SESSION_ID",
    )
    session.add(orphaned_subscription)
    session.commit()

    response = client.post(
        "v1/user",
        json={
            "name": "A Parent With Subscription",
            "email": email,
            "type": "parent",
            "checkout_session_id": "TEST_CHECKOUT_SESSION_ID",
        },
        headers=backend_service_account_headers,
    )
    assert response.status_code == status.HTTP_200_OK

    json = response.json()
    assert json["subscription"]["provider"] == "stripe"
    assert json["subscription"]["is_active"] is True
    assert json["subscription"]["type"] == "family"
    assert json["subscription"]["stripe_customer_id"] == "cus_123"
    assert json["subscription"]["id"] == "sub_123"
    assert json["subscription"]["product"]["id"] == test_product.id

    email_2 = "testemail2@site.com"
    if existing_user := crud.user.get_by_account_email(db=session, email=email_2):
        crud.user.remove(db=session, id=existing_user.id)

    response = client.post(
        "v1/user",
        json={
            "name": "A Parent Trying to Steal a Subscription",
            "email": email_2,
            "type": "parent",
            "checkout_session_id": "TEST_CHECKOUT_SESSION_ID",
        },
        headers=backend_service_account_headers,
    )
    assert response.status_code == status.HTTP_200_OK

    json = response.json()
    assert json["subscription"] is None

    subscription_repository.delete(db=session, subscription_id=orphaned_subscription.id)
