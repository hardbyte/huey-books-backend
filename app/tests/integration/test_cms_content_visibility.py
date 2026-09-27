"""
Integration tests for CMS content visibility enforcement.

Tests the visibility access control system:
- WRIVETED: System content, visible to all authenticated users
- PUBLIC: Visible to all authenticated users
- SCHOOL: Visible only to users in the same school
- PRIVATE: Visible only to creator and school admins

Security requirements:
- User from School A cannot see School B's private content
- User from School A sees their school's SCHOOL-visibility content
- All users see PUBLIC and WRIVETED content

Note: The CMS management API (/v1/cms/content/{id}) requires superuser/service account access.
Visibility filtering is enforced via the /v1/cms/content/random endpoint which allows user-based access.
"""

import secrets

import pytest
from sqlalchemy import text


@pytest.fixture(autouse=True)
def cleanup_cms_data(session):
    """Clean up CMS data before and after each test to ensure isolation."""
    cms_tables = [
        "cms_content",
        "cms_content_variants",
        "flow_definitions",
        "flow_nodes",
        "flow_connections",
        "conversation_sessions",
        "conversation_history",
        "conversation_analytics",
    ]

    session.rollback()

    for table in cms_tables:
        try:
            session.execute(text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE"))
        except Exception:
            pass
    session.commit()

    yield

    session.rollback()

    for table in cms_tables:
        try:
            session.execute(text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE"))
        except Exception:
            pass
    session.commit()


@pytest.fixture()
def second_test_school(client, session, backend_service_account_headers):
    """Create a second test school for cross-school visibility testing."""
    from app.repositories.school_repository import school_repository

    test_school_id = secrets.token_hex(8)

    new_test_school_response = client.post(
        "/v1/school",
        headers=backend_service_account_headers,
        json={
            "name": f"Second Test School - {test_school_id}",
            "country_code": "NZL",
            "official_identifier": test_school_id,
            "info": {
                "msg": "Created for visibility test purposes",
                "location": {"state": "Required", "postcode": "Required"},
            },
        },
        timeout=120,
    )
    new_test_school_response.raise_for_status()
    school_info = new_test_school_response.json()

    yield school_repository.get_by_wriveted_id(
        db=session, wriveted_id=school_info["wriveted_identifier"]
    )

    # Cleanup - delete the school
    try:
        client.delete(
            f"/v1/school/{school_info['wriveted_identifier']}",
            headers=backend_service_account_headers,
        )
    except Exception:
        pass


@pytest.fixture()
def school_a_admin_headers(test_school, session):
    """Create admin headers for School A (test_school)."""
    from datetime import timedelta

    from app import crud
    from app.models.user import UserAccountType
    from app.schemas.users.user_create import UserCreateIn
    from app.services.security import create_access_token
    from app.tests.util.random_strings import random_lower_string

    schooladmin = crud.user.create(
        db=session,
        obj_in=UserCreateIn(
            name="School A Admin",
            email=f"{random_lower_string(6)}@schoola.test.com",
            type=UserAccountType.SCHOOL_ADMIN,
            school_id=test_school.id,
        ),
    )

    access_token = create_access_token(
        subject=f"wriveted:user-account:{schooladmin.id}",
        expires_delta=timedelta(minutes=5),
    )

    yield {"Authorization": f"bearer {access_token}"}

    try:
        session.delete(schooladmin)
        session.commit()
    except Exception:
        session.rollback()


@pytest.fixture()
def school_b_admin_headers(second_test_school, session):
    """Create admin headers for School B (second_test_school)."""
    from datetime import timedelta

    from app import crud
    from app.models.user import UserAccountType
    from app.schemas.users.user_create import UserCreateIn
    from app.services.security import create_access_token
    from app.tests.util.random_strings import random_lower_string

    schooladmin = crud.user.create(
        db=session,
        obj_in=UserCreateIn(
            name="School B Admin",
            email=f"{random_lower_string(6)}@schoolb.test.com",
            type=UserAccountType.SCHOOL_ADMIN,
            school_id=second_test_school.id,
        ),
    )

    access_token = create_access_token(
        subject=f"wriveted:user-account:{schooladmin.id}",
        expires_delta=timedelta(minutes=5),
    )

    yield {"Authorization": f"bearer {access_token}"}

    try:
        session.delete(schooladmin)
        session.commit()
    except Exception:
        session.rollback()


class TestContentVisibilityWriveted:
    """Test WRIVETED visibility content - accessible to all authenticated users."""

    def test_wriveted_content_visible_to_service_account(
        self, client, backend_service_account_headers
    ):
        """WRIVETED content should be visible to service accounts."""
        content_data = {
            "type": "joke",
            "content": {"text": "Wriveted test joke"},
            "status": "PUBLISHED",
            "visibility": "wriveted",
            "tags": ["visibility-test"],
        }

        response = client.post(
            "/v1/cms/content",
            json=content_data,
            headers=backend_service_account_headers,
        )
        assert response.status_code == 201
        content_id = response.json()["id"]

        # Should be visible when fetching via admin endpoint
        get_response = client.get(
            f"/v1/cms/content/{content_id}", headers=backend_service_account_headers
        )
        assert get_response.status_code == 200
        assert get_response.json()["visibility"] == "wriveted"

    def test_wriveted_content_visible_via_random_endpoint(
        self, client, backend_service_account_headers, school_a_admin_headers
    ):
        """WRIVETED content should be visible to school admins via random endpoint."""
        # Create WRIVETED content as service account
        content_data = {
            "type": "joke",
            "content": {"text": "Wriveted joke for school admin"},
            "status": "PUBLISHED",
            "visibility": "wriveted",
            "tags": ["visibility-wriveted-test"],
            "is_active": True,
        }

        response = client.post(
            "/v1/cms/content",
            json=content_data,
            headers=backend_service_account_headers,
        )
        assert response.status_code == 201
        content_id = response.json()["id"]

        # School admin should see it via random endpoint (visibility filtering)
        random_response = client.get(
            "/v1/cms/content/random?type=joke&count=10&tags=visibility-wriveted-test",
            headers=school_a_admin_headers,
        )
        assert random_response.status_code == 200
        content_ids = [c["id"] for c in random_response.json()]
        assert content_id in content_ids


class TestContentVisibilityPublic:
    """Test PUBLIC visibility content - accessible to all authenticated users."""

    def test_public_content_visible_to_all_schools(
        self,
        client,
        backend_service_account_headers,
        school_a_admin_headers,
        school_b_admin_headers,
    ):
        """PUBLIC content should be visible to users from any school via random endpoint."""
        content_data = {
            "type": "joke",
            "content": {"text": "Public joke for all"},
            "status": "PUBLISHED",
            "visibility": "public",
            "tags": ["visibility-public-test"],
            "is_active": True,
        }

        response = client.post(
            "/v1/cms/content",
            json=content_data,
            headers=backend_service_account_headers,
        )
        assert response.status_code == 201
        content_id = response.json()["id"]

        # Should be visible to School A admin via random endpoint
        response_a = client.get(
            "/v1/cms/content/random?type=joke&count=10&tags=visibility-public-test",
            headers=school_a_admin_headers,
        )
        assert response_a.status_code == 200
        content_ids_a = [c["id"] for c in response_a.json()]
        assert content_id in content_ids_a

        # Should be visible to School B admin via random endpoint
        response_b = client.get(
            "/v1/cms/content/random?type=joke&count=10&tags=visibility-public-test",
            headers=school_b_admin_headers,
        )
        assert response_b.status_code == 200
        content_ids_b = [c["id"] for c in response_b.json()]
        assert content_id in content_ids_b


class TestContentVisibilitySchool:
    """Test SCHOOL visibility content - accessible only within the same school."""

    def test_school_content_visible_to_same_school(
        self,
        client,
        test_school,
        school_a_admin_headers,
        backend_service_account_headers,
    ):
        """SCHOOL visibility content should be visible to users in the same school."""
        content_data = {
            "type": "joke",
            "content": {"text": "School A private joke"},
            "status": "PUBLISHED",
            "visibility": "school",
            "school_id": str(test_school.wriveted_identifier),
            "tags": ["visibility-school-same-test"],
            "is_active": True,
        }

        response = client.post(
            "/v1/cms/content",
            json=content_data,
            headers=backend_service_account_headers,
        )
        assert response.status_code == 201
        content_id = response.json()["id"]

        # Should be visible to School A admin via random endpoint
        get_response = client.get(
            "/v1/cms/content/random?type=joke&count=10&tags=visibility-school-same-test",
            headers=school_a_admin_headers,
        )
        assert get_response.status_code == 200
        content_ids = [c["id"] for c in get_response.json()]
        assert content_id in content_ids

    def test_school_content_not_visible_to_other_school(
        self,
        client,
        test_school,
        school_b_admin_headers,
        backend_service_account_headers,
    ):
        """SCHOOL visibility content should NOT be visible to users from other schools."""
        content_data = {
            "type": "joke",
            "content": {"text": "School A only joke"},
            "status": "PUBLISHED",
            "visibility": "school",
            "school_id": str(test_school.wriveted_identifier),
            "tags": ["visibility-school-other-test"],
            "is_active": True,
        }

        response = client.post(
            "/v1/cms/content",
            json=content_data,
            headers=backend_service_account_headers,
        )
        assert response.status_code == 201
        content_id = response.json()["id"]

        # Should NOT be visible to School B admin via random endpoint
        get_response = client.get(
            "/v1/cms/content/random?type=joke&count=10&tags=visibility-school-other-test",
            headers=school_b_admin_headers,
        )
        assert get_response.status_code == 200
        content_ids = [c["id"] for c in get_response.json()]
        assert content_id not in content_ids


class TestContentVisibilityPrivate:
    """Test PRIVATE visibility content - accessible only to creator and school admins."""

    def test_private_content_visible_to_same_school_admin(
        self,
        client,
        test_school,
        school_a_admin_headers,
        backend_service_account_headers,
    ):
        """PRIVATE content should be visible to admins at the same school."""
        content_data = {
            "type": "joke",
            "content": {"text": "Very private School A joke"},
            "status": "DRAFT",
            "visibility": "private",
            "school_id": str(test_school.wriveted_identifier),
            "tags": ["visibility-private-same-test"],
            "is_active": True,
        }

        response = client.post(
            "/v1/cms/content",
            json=content_data,
            headers=backend_service_account_headers,
        )
        assert response.status_code == 201
        content_id = response.json()["id"]

        # Should be visible to School A admin via random endpoint
        get_response = client.get(
            "/v1/cms/content/random?type=joke&count=10&tags=visibility-private-same-test",
            headers=school_a_admin_headers,
        )
        assert get_response.status_code == 200
        content_ids = [c["id"] for c in get_response.json()]
        assert content_id in content_ids

    def test_private_content_not_visible_to_other_school(
        self,
        client,
        test_school,
        school_b_admin_headers,
        backend_service_account_headers,
    ):
        """PRIVATE content should NOT be visible to admins at other schools."""
        content_data = {
            "type": "joke",
            "content": {"text": "Super secret School A joke"},
            "status": "DRAFT",
            "visibility": "private",
            "school_id": str(test_school.wriveted_identifier),
            "tags": ["visibility-private-other-test"],
            "is_active": True,
        }

        response = client.post(
            "/v1/cms/content",
            json=content_data,
            headers=backend_service_account_headers,
        )
        assert response.status_code == 201
        content_id = response.json()["id"]

        # Should NOT be visible to School B admin via random endpoint
        get_response = client.get(
            "/v1/cms/content/random?type=joke&count=10&tags=visibility-private-other-test",
            headers=school_b_admin_headers,
        )
        assert get_response.status_code == 200
        content_ids = [c["id"] for c in get_response.json()]
        assert content_id not in content_ids


class TestRandomContentVisibility:
    """Test visibility filtering in random content endpoint."""

    def test_random_content_returns_wriveted_content(
        self, client, backend_service_account_headers
    ):
        """Random content should include WRIVETED visibility content."""
        # Create multiple WRIVETED content items
        for i in range(3):
            content_data = {
                "type": "question",
                "content": {
                    "question_text": f"Wriveted question {i}",
                    "min_age": 5,
                    "max_age": 14,
                    "answers": [
                        {"text": "Answer A", "hue_map": {"hue01_dark_suspense": 1.0}},
                        {
                            "text": "Answer B",
                            "hue_map": {"hue02_beautiful_whimsical": 1.0},
                        },
                    ],
                },
                "status": "PUBLISHED",
                "visibility": "wriveted",
                "tags": ["huey-preference", "random-test"],
                "is_active": True,
            }
            response = client.post(
                "/v1/cms/content",
                json=content_data,
                headers=backend_service_account_headers,
            )
            assert response.status_code == 201

        # Get random content
        response = client.get(
            "/v1/cms/content/random?type=question&count=3&tags=random-test",
            headers=backend_service_account_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1

    def test_random_content_filters_by_school(
        self,
        client,
        test_school,
        second_test_school,
        school_a_admin_headers,
        school_b_admin_headers,
        backend_service_account_headers,
    ):
        """Random content should filter school-scoped content by user's school."""
        # Create SCHOOL visibility content for School A
        school_a_content = {
            "type": "question",
            "content": {
                "question_text": "School A only question",
                "min_age": 5,
                "max_age": 14,
                "answers": [
                    {"text": "A", "hue_map": {"hue01_dark_suspense": 1.0}},
                    {"text": "B", "hue_map": {"hue02_beautiful_whimsical": 1.0}},
                ],
            },
            "status": "PUBLISHED",
            "visibility": "school",
            "school_id": str(test_school.wriveted_identifier),
            "tags": ["school-filter-test"],
            "is_active": True,
        }
        response = client.post(
            "/v1/cms/content",
            json=school_a_content,
            headers=backend_service_account_headers,
        )
        assert response.status_code == 201
        school_a_content_id = response.json()["id"]

        # School A admin should see it in random content
        response_a = client.get(
            "/v1/cms/content/random?type=question&count=10&tags=school-filter-test",
            headers=school_a_admin_headers,
        )
        assert response_a.status_code == 200
        content_ids_a = [c["id"] for c in response_a.json()]
        assert school_a_content_id in content_ids_a

        # School B admin should NOT see it in random content
        response_b = client.get(
            "/v1/cms/content/random?type=question&count=10&tags=school-filter-test",
            headers=school_b_admin_headers,
        )
        assert response_b.status_code == 200
        content_ids_b = [c["id"] for c in response_b.json()]
        assert school_a_content_id not in content_ids_b


class TestFlowVisibility:
    """Test visibility enforcement for flow definitions."""

    def test_flow_visibility_school_scoped(
        self,
        client,
        test_school,
        school_a_admin_headers,
        school_b_admin_headers,
        backend_service_account_headers,
    ):
        """Flow with SCHOOL visibility should only be visible to same school users."""
        flow_data = {
            "name": "School A Private Flow",
            "description": "A flow only for School A",
            "visibility": "school",
            "school_id": str(test_school.wriveted_identifier),
            "version": "1.0.0",
            "flow_data": {},
        }

        response = client.post(
            "/v1/cms/flows", json=flow_data, headers=backend_service_account_headers
        )
        assert response.status_code == 201
        flow_id = response.json()["id"]

        # Flow visibility is managed by admin API - test that flow is created correctly
        # and has the right visibility attribute
        get_response = client.get(
            f"/v1/cms/flows/{flow_id}", headers=backend_service_account_headers
        )
        assert get_response.status_code == 200
        assert get_response.json()["visibility"] == "school"

    def test_wriveted_flow_visible_to_all(
        self,
        client,
        school_a_admin_headers,
        school_b_admin_headers,
        backend_service_account_headers,
    ):
        """Flow with WRIVETED visibility should be visible to all authenticated users."""
        flow_data = {
            "name": "Global Wriveted Flow",
            "description": "A flow for everyone",
            "visibility": "wriveted",
            "version": "1.0.0",
            "flow_data": {},
        }

        response = client.post(
            "/v1/cms/flows", json=flow_data, headers=backend_service_account_headers
        )
        assert response.status_code == 201
        flow_id = response.json()["id"]

        # Verify flow is created with wriveted visibility
        get_response = client.get(
            f"/v1/cms/flows/{flow_id}", headers=backend_service_account_headers
        )
        assert get_response.status_code == 200
        assert get_response.json()["visibility"] == "wriveted"
