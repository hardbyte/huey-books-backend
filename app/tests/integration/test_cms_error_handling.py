"""
Comprehensive error handling tests for CMS API endpoints.

This module tests all the "unhappy path" scenarios that should return proper
HTTP error codes for permission failures, malformed data, and conflict scenarios.
"""

import uuid

import pytest
from sqlalchemy import text
from starlette import status


@pytest.fixture(autouse=True)
def cleanup_cms_data(session):
    """Clean up CMS data before and after each test to ensure test isolation.

    Uses synchronous session since tests use synchronous client fixture.
    """
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

    # Clean up before test runs
    for table in cms_tables:
        try:
            session.execute(text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE"))
        except Exception:
            # Table might not exist, skip it
            pass
    session.commit()

    yield

    session.rollback()

    # Clean up after test runs
    for table in cms_tables:
        try:
            session.execute(text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE"))
        except Exception:
            # Table might not exist, skip it
            pass
    session.commit()


# =============================================================================
# Permission Failure Tests (403 Forbidden)
# =============================================================================


def test_student_cannot_list_cms_content(client, test_student_user_account_headers):
    """Test that student users get 403 Forbidden when accessing CMS content endpoints."""
    response = client.get("v1/cms/content", headers=test_student_user_account_headers)
    assert response.status_code == status.HTTP_403_FORBIDDEN
    error_detail = response.json()["detail"]
    assert "privileges" in error_detail.lower()


def test_student_cannot_create_cms_content(client, test_student_user_account_headers):
    """Test that student users get 403 Forbidden when creating CMS content."""
    content_data = {
        "type": "joke",
        "content": {"text": "Students shouldn't be able to create this"},
        "tags": ["unauthorized"],
    }

    response = client.post(
        "v1/cms/content", json=content_data, headers=test_student_user_account_headers
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    error_detail = response.json()["detail"]
    assert "privileges" in error_detail.lower()


def test_student_cannot_update_cms_content(
    client, test_student_user_account_headers, backend_service_account_headers
):
    """Test that student users get 403 Forbidden when updating CMS content."""
    # First create content with backend service account
    content_data = {
        "type": "fact",
        "content": {"text": "Original content for update test"},
    }

    create_response = client.post(
        "v1/cms/content", json=content_data, headers=backend_service_account_headers
    )
    content_id = create_response.json()["id"]

    # Try to update with student account
    update_data = {"content": {"text": "Student tried to update this"}}

    response = client.put(
        f"v1/cms/content/{content_id}",
        json=update_data,
        headers=test_student_user_account_headers,
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    error_detail = response.json()["detail"]
    assert "privileges" in error_detail.lower()


def test_student_cannot_delete_cms_content(
    client, test_student_user_account_headers, backend_service_account_headers
):
    """Test that student users get 403 Forbidden when deleting CMS content."""
    # First create content with backend service account
    content_data = {
        "type": "message",
        "content": {"text": "Content for deletion test"},
    }

    create_response = client.post(
        "v1/cms/content", json=content_data, headers=backend_service_account_headers
    )
    content_id = create_response.json()["id"]

    # Try to delete with student account
    response = client.delete(
        f"v1/cms/content/{content_id}",
        headers=test_student_user_account_headers,
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    error_detail = response.json()["detail"]
    assert "privileges" in error_detail.lower()


def test_regular_user_cannot_access_cms_flows(client, test_user_account_headers):
    """Test that regular users get 403 Forbidden when accessing CMS flows."""
    response = client.get("v1/cms/flows", headers=test_user_account_headers)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    error_detail = response.json()["detail"]
    assert "privileges" in error_detail.lower()


def test_regular_user_cannot_create_cms_flows(client, test_user_account_headers):
    """Test that regular users get 403 Forbidden when creating CMS flows."""
    flow_data = {
        "name": "Unauthorized Flow",
        "description": "This should not be allowed",
        "version": "1.0",
        "flow_data": {},
        "entry_node_id": "start",
    }

    response = client.post(
        "v1/cms/flows", json=flow_data, headers=test_user_account_headers
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    error_detail = response.json()["detail"]
    assert "privileges" in error_detail.lower()


def test_school_admin_cannot_access_cms_endpoints(
    client, test_schooladmin_account_headers
):
    """Test that school admin users get 403 Forbidden when accessing CMS endpoints."""
    # Test various CMS endpoints that should be restricted to superuser/backend accounts only
    endpoints_and_methods = [
        ("v1/cms/content", "GET"),
        ("v1/cms/flows", "GET"),
    ]

    for endpoint, method in endpoints_and_methods:
        if method == "GET":
            response = client.get(endpoint, headers=test_schooladmin_account_headers)
        elif method == "POST":
            response = client.post(
                endpoint,
                json={"test": "data"},
                headers=test_schooladmin_account_headers,
            )

        assert (
            response.status_code == status.HTTP_403_FORBIDDEN
        ), f"{method} {endpoint} should return 403 for school admin"
        error_detail = response.json()["detail"]
        assert "privileges" in error_detail.lower()


def test_student_cannot_create_flow_nodes(
    client, test_student_user_account_headers, backend_service_account_headers
):
    """Test that students cannot create flow nodes even if they have flow ID."""
    # Create a flow with backend service account
    flow_data = {
        "name": "Test Flow for Node Permission Test",
        "version": "1.0",
        "flow_data": {},
        "entry_node_id": "test_node",
    }

    flow_response = client.post(
        "v1/cms/flows", json=flow_data, headers=backend_service_account_headers
    )
    flow_id = flow_response.json()["id"]

    # Try to create node with student account
    node_data = {
        "node_id": "unauthorized_node",
        "node_type": "message",
        "content": {"messages": [{"content": "Student should not create this"}]},
    }

    response = client.post(
        f"v1/cms/flows/{flow_id}/nodes",
        json=node_data,
        headers=test_student_user_account_headers,
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    error_detail = response.json()["detail"]
    assert "privileges" in error_detail.lower()


# =============================================================================
# Malformed Data Tests (422 Unprocessable Entity)
# =============================================================================


def test_create_content_missing_required_fields(
    client, backend_service_account_headers
):
    """Test creating content with missing required fields returns 422."""
    malformed_content = {
        # Missing required "type" field
        "content": {"text": "This should fail"},
        "tags": ["malformed"],
    }

    response = client.post(
        "v1/cms/content",
        json=malformed_content,
        headers=backend_service_account_headers,
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    error_detail = response.json()["detail"]
    # Should mention the missing field
    assert any("type" in str(error).lower() for error in error_detail)


def test_create_content_invalid_content_type(client, backend_service_account_headers):
    """Test creating content with invalid content type returns 422."""
    invalid_content = {
        "type": "totally_invalid_content_type",
        "content": {"text": "This should fail due to invalid type"},
    }

    response = client.post(
        "v1/cms/content", json=invalid_content, headers=backend_service_account_headers
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


def test_create_content_invalid_data_types(client, backend_service_account_headers):
    """Test creating content with wrong data types returns 422."""
    invalid_content = {
        "type": "joke",
        "content": "This should be a dict, not a string",  # Wrong type
        "tags": "this_should_be_array",  # Wrong type
    }

    response = client.post(
        "v1/cms/content", json=invalid_content, headers=backend_service_account_headers
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


def test_create_flow_missing_required_fields(client, backend_service_account_headers):
    """Test creating flow with missing required fields returns 422."""
    malformed_flow = {
        # Missing required "name" field
        "version": "1.0",
        "flow_data": {},
    }

    response = client.post(
        "v1/cms/flows", json=malformed_flow, headers=backend_service_account_headers
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    error_detail = response.json()["detail"]
    # Should mention the missing field
    assert any("name" in str(error).lower() for error in error_detail)


def test_create_flow_node_missing_required_content_fields(
    client, backend_service_account_headers
):
    """Test creating flow node with missing required content fields returns 422."""
    # Create a flow first
    flow_data = {
        "name": "Flow for Node Validation Test",
        "version": "1.0",
        "flow_data": {},
        "entry_node_id": "test_node",
    }

    flow_response = client.post(
        "v1/cms/flows", json=flow_data, headers=backend_service_account_headers
    )
    flow_id = flow_response.json()["id"]

    # Try to create node with missing required fields
    malformed_node = {
        "node_id": "test_node",
        "node_type": "message",
        # Missing required "content" field
        "position": {"x": 0, "y": 0},
    }

    response = client.post(
        f"v1/cms/flows/{flow_id}/nodes",
        json=malformed_node,
        headers=backend_service_account_headers,
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    error_detail = response.json()["detail"]
    assert any("content" in str(error).lower() for error in error_detail)


def test_create_flow_node_invalid_node_type(client, backend_service_account_headers):
    """Test creating flow node with invalid node type returns 422."""
    # Create a flow first
    flow_data = {
        "name": "Flow for Node Type Validation Test",
        "version": "1.0",
        "flow_data": {},
        "entry_node_id": "test_node",
    }

    flow_response = client.post(
        "v1/cms/flows", json=flow_data, headers=backend_service_account_headers
    )
    flow_id = flow_response.json()["id"]

    # Try to create node with invalid node type
    invalid_node = {
        "node_id": "test_node",
        "node_type": "totally_invalid_node_type",
        "content": {"messages": [{"content": "Test message"}]},
    }

    response = client.post(
        f"v1/cms/flows/{flow_id}/nodes",
        json=invalid_node,
        headers=backend_service_account_headers,
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


def test_update_content_invalid_uuid(client, backend_service_account_headers):
    """Test updating content with invalid UUID returns 422."""
    invalid_uuid = "not-a-valid-uuid"
    update_data = {"content": {"text": "This should fail"}}

    response = client.put(
        f"v1/cms/content/{invalid_uuid}",
        json=update_data,
        headers=backend_service_account_headers,
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


def test_create_content_variant_missing_fields(client, backend_service_account_headers):
    """Test creating content variant with missing required fields returns 422."""
    # Create content first
    content_data = {
        "type": "joke",
        "content": {"text": "Base content for variant test"},
    }

    create_response = client.post(
        "v1/cms/content", json=content_data, headers=backend_service_account_headers
    )
    content_id = create_response.json()["id"]

    # Try to create variant with missing required fields
    malformed_variant = {
        # Missing required "variant_key" field
        "variant_data": {"text": "This should fail"},
    }

    response = client.post(
        f"v1/cms/content/{content_id}/variants",
        json=malformed_variant,
        headers=backend_service_account_headers,
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    error_detail = response.json()["detail"]
    assert any("variant_key" in str(error).lower() for error in error_detail)


def test_create_flow_connection_missing_fields(client, backend_service_account_headers):
    """Test creating flow connection with missing required fields returns 422."""
    # Create flow first
    flow_data = {
        "name": "Flow for Connection Validation Test",
        "version": "1.0",
        "flow_data": {},
        "entry_node_id": "start",
    }

    flow_response = client.post(
        "v1/cms/flows", json=flow_data, headers=backend_service_account_headers
    )
    flow_id = flow_response.json()["id"]

    # Try to create connection with missing required fields
    malformed_connection = {
        # Missing required "target_node_id" field
        "source_node_id": "start",
        "connection_type": "default",
    }

    response = client.post(
        f"v1/cms/flows/{flow_id}/connections",
        json=malformed_connection,
        headers=backend_service_account_headers,
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    error_detail = response.json()["detail"]
    assert any("target_node_id" in str(error).lower() for error in error_detail)


# =============================================================================
# Conflict and Resource State Tests (409 Conflict)
# =============================================================================


def test_delete_nonexistent_content_returns_404(
    client, backend_service_account_headers
):
    """Test deleting non-existent content returns 404 Not Found."""
    fake_content_id = str(uuid.uuid4())

    response = client.delete(
        f"v1/cms/content/{fake_content_id}",
        headers=backend_service_account_headers,
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    error_detail = response.json()["detail"]
    assert "not found" in error_detail.lower()


def test_update_nonexistent_flow_returns_404(client, backend_service_account_headers):
    """Test updating non-existent flow returns 404 Not Found."""
    fake_flow_id = str(uuid.uuid4())
    update_data = {"name": "This should fail"}

    response = client.put(
        f"v1/cms/flows/{fake_flow_id}",
        json=update_data,
        headers=backend_service_account_headers,
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    error_detail = response.json()["detail"]
    assert "not found" in error_detail.lower()


def test_create_node_for_nonexistent_flow_returns_404(
    client, backend_service_account_headers
):
    """Test creating node for non-existent flow returns 404 Not Found."""
    fake_flow_id = str(uuid.uuid4())
    node_data = {
        "node_id": "test_node",
        "node_type": "message",
        "content": {"messages": [{"content": "This should fail"}]},
    }

    response = client.post(
        f"v1/cms/flows/{fake_flow_id}/nodes",
        json=node_data,
        headers=backend_service_account_headers,
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    error_detail = response.json()["detail"]
    assert "not found" in error_detail.lower()


def test_update_nonexistent_flow_node_returns_404(
    client, backend_service_account_headers
):
    """Test updating non-existent flow node returns 404 Not Found."""
    # Create a flow first
    flow_data = {
        "name": "Flow for Non-existent Node Test",
        "version": "1.0",
        "flow_data": {},
        "entry_node_id": "start",
    }

    flow_response = client.post(
        "v1/cms/flows", json=flow_data, headers=backend_service_account_headers
    )
    flow_id = flow_response.json()["id"]

    # Try to update non-existent node - use valid UUID format that doesn't exist
    fake_node_id = "7a258eeb-0146-477e-a7f6-fc642f3c7d20"
    update_data = {"content": {"messages": [{"content": "This should fail"}]}}

    response = client.put(
        f"v1/cms/flows/{flow_id}/nodes/{fake_node_id}",
        json=update_data,
        headers=backend_service_account_headers,
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    error_detail = response.json()["detail"]
    assert "not found" in error_detail.lower()


def test_create_variant_for_nonexistent_content_returns_404(
    client, backend_service_account_headers
):
    """Test creating variant for non-existent content returns 404 Not Found."""
    fake_content_id = str(uuid.uuid4())
    variant_data = {
        "variant_key": "test_variant",
        "variant_data": {"text": "This should fail"},
    }

    response = client.post(
        f"v1/cms/content/{fake_content_id}/variants",
        json=variant_data,
        headers=backend_service_account_headers,
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    error_detail = response.json()["detail"]
    assert "not found" in error_detail.lower()


def test_update_content_status_nonexistent_content(
    client, backend_service_account_headers
):
    """Test updating status of non-existent content returns 404 Not Found."""
    fake_content_id = str(uuid.uuid4())
    status_update = {"status": "published", "comment": "This should fail"}

    response = client.post(
        f"v1/cms/content/{fake_content_id}/status",
        json=status_update,
        headers=backend_service_account_headers,
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    error_detail = response.json()["detail"]
    assert "not found" in error_detail.lower()


def test_publish_nonexistent_flow_returns_404(client, backend_service_account_headers):
    """Test publishing non-existent flow returns 404 Not Found."""
    fake_flow_id = str(uuid.uuid4())
    publish_data = {"publish": True}

    response = client.put(
        f"v1/cms/flows/{fake_flow_id}",
        json=publish_data,
        headers=backend_service_account_headers,
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    error_detail = response.json()["detail"]
    assert "not found" in error_detail.lower()


def test_clone_nonexistent_flow_returns_404(client, backend_service_account_headers):
    """Test cloning non-existent flow returns 404 Not Found."""
    fake_flow_id = str(uuid.uuid4())
    clone_data = {"name": "Cloned Flow", "version": "1.1"}

    response = client.post(
        f"v1/cms/flows/{fake_flow_id}/clone",
        json=clone_data,
        headers=backend_service_account_headers,
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    error_detail = response.json()["detail"]
    assert "not found" in error_detail.lower()


# =============================================================================
# Edge Cases and Error Boundary Tests
# =============================================================================


def test_update_variant_with_wrong_content_id(client, backend_service_account_headers):
    """Test updating variant with wrong content ID returns 404."""
    # Create content and variant
    content_data = {
        "type": "joke",
        "content": {"text": "Base content for variant test"},
    }

    create_response = client.post(
        "v1/cms/content", json=content_data, headers=backend_service_account_headers
    )
    content_id = create_response.json()["id"]

    # Create variant
    variant_data = {
        "variant_key": "test_variant",
        "variant_data": {"text": "Test variant"},
    }

    variant_response = client.post(
        f"v1/cms/content/{content_id}/variants",
        json=variant_data,
        headers=backend_service_account_headers,
    )
    variant_id = variant_response.json()["id"]

    # Try to update variant with wrong content ID
    wrong_content_id = str(uuid.uuid4())
    update_data = {"variant_data": {"text": "Updated variant"}}

    response = client.put(
        f"v1/cms/content/{wrong_content_id}/variants/{variant_id}",
        json=update_data,
        headers=backend_service_account_headers,
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    error_detail = response.json()["detail"]
    assert "not found" in error_detail.lower()


def test_delete_connection_with_wrong_flow_id(client, backend_service_account_headers):
    """Test deleting connection with wrong flow ID returns 404."""
    # Create flow and connection
    flow_data = {
        "name": "Flow for Connection Test",
        "version": "1.0",
        "flow_data": {},
        "entry_node_id": "start",
    }

    flow_response = client.post(
        "v1/cms/flows", json=flow_data, headers=backend_service_account_headers
    )
    flow_id = flow_response.json()["id"]

    # Create nodes
    for node_id in ["start", "end"]:
        client.post(
            f"v1/cms/flows/{flow_id}/nodes",
            json={
                "node_id": node_id,
                "node_type": "message",
                "content": {"messages": [{"content": f"Node {node_id}"}]},
            },
            headers=backend_service_account_headers,
        )

    # Create connection
    connection_response = client.post(
        f"v1/cms/flows/{flow_id}/connections",
        json={
            "source_node_id": "start",
            "target_node_id": "end",
            "connection_type": "default",
        },
        headers=backend_service_account_headers,
    )
    connection_id = connection_response.json()["id"]

    # Try to delete connection with wrong flow ID
    wrong_flow_id = str(uuid.uuid4())

    response = client.delete(
        f"v1/cms/flows/{wrong_flow_id}/connections/{connection_id}",
        headers=backend_service_account_headers,
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    error_detail = response.json()["detail"]
    assert "not found" in error_detail.lower()


def test_create_content_with_extremely_long_tags(
    client, backend_service_account_headers
):
    """Test creating content with extremely long tags might cause validation issues."""
    # Create content with extremely long tag names
    extremely_long_tag = "x" * 1000  # 1000 character tag

    content_data = {
        "type": "joke",
        "content": {"text": "Test content with long tags"},
        "tags": [extremely_long_tag, "normal_tag"],
    }

    response = client.post(
        "v1/cms/content", json=content_data, headers=backend_service_account_headers
    )

    # This could either succeed (if no validation) or fail with 422 (if validation exists)
    # We're testing that the API handles it gracefully either way
    assert response.status_code in [
        status.HTTP_201_CREATED,
        status.HTTP_422_UNPROCESSABLE_CONTENT,
    ]


def test_create_flow_with_circular_reference_data(
    client, backend_service_account_headers
):
    """Test creating flow with complex nested data doesn't cause server errors."""
    # Create flow with deeply nested flow_data
    deeply_nested_data = {
        "level1": {"level2": {"level3": {"level4": {"level5": "deep_value"}}}}
    }

    flow_data = {
        "name": "Deep Nested Flow",
        "version": "1.0",
        "flow_data": deeply_nested_data,
        "entry_node_id": "start",
    }

    response = client.post(
        "v1/cms/flows", json=flow_data, headers=backend_service_account_headers
    )

    # Should either succeed or fail gracefully with validation error
    assert response.status_code in [
        status.HTTP_201_CREATED,
        status.HTTP_422_UNPROCESSABLE_CONTENT,
    ]

    # If it succeeded, the nested data should be preserved
    if response.status_code == status.HTTP_201_CREATED:
        data = response.json()
        assert (
            data["flow_data"]["level1"]["level2"]["level3"]["level4"]["level5"]
            == "deep_value"
        )
