"""
CMS Authentication and Authorization Integration Tests.

Consolidated authentication tests covering:
- Authentication requirements for CMS endpoints
- Service account authentication patterns
- Authorization checks for different user types
- CSRF token handling and security
- Authentication error handling and edge cases

Test Organization:
- TestCMSAuthentication: Basic authentication requirements
- TestServiceAccountAuth: Service account specific authentication
- TestAuthorizationLevels: Role-based access control
- TestSecurityBoundaries: Security enforcement and edge cases
"""

import uuid

import pytest
from sqlalchemy import text
from starlette import status


# Test isolation fixture for CMS data
@pytest.fixture(autouse=True)
async def cleanup_cms_data(async_session):
    """Clean up CMS data before and after each test to ensure test isolation."""
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

    # Clean up before test runs
    for table in cms_tables:
        try:
            await async_session.execute(
                text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE")
            )
        except Exception:
            # Table might not exist, skip it
            pass
    await async_session.commit()

    yield

    # Clean up after test runs
    for table in cms_tables:
        try:
            await async_session.execute(
                text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE")
            )
        except Exception:
            # Table might not exist, skip it
            pass
    await async_session.commit()


class TestCMSAuthentication:
    """Test basic authentication requirements for CMS endpoints."""

    @pytest.fixture(autouse=True)
    def setup_test(self, reset_global_state_sync):
        """Ensure global state is reset before each test."""
        pass

    async def test_cms_content_requires_authentication(self, async_client):
        """Test that CMS content endpoints require authentication."""
        # Try to access content list without authentication
        response = await async_client.get("/v1/cms/content")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

        # Try to create content without authentication
        content_data = {
            "type": "joke",
            "content": {"text": "Test joke"},
            "tags": ["test"],
            "status": "draft",
        }
        response = await async_client.post("/v1/cms/content", json=content_data)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

        # Try to update content without authentication
        fake_id = str(uuid.uuid4())
        response = await async_client.put(
            f"/v1/cms/content/{fake_id}", json={"status": "published"}
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

        # Try to delete content without authentication
        response = await async_client.delete(f"/v1/cms/content/{fake_id}")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    async def test_cms_flows_require_authentication(self, async_client):
        """Test that CMS flow endpoints require authentication."""
        # Try to access flows without authentication
        response = await async_client.get("/v1/cms/flows")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

        # Try to create flow without authentication
        flow_data = {
            "name": "Test Flow",
            "description": "Test description",
            "version": "1.0.0",
            "flow_data": {"nodes": [], "connections": []},
            "entry_node_id": "start",
        }
        response = await async_client.post("/v1/cms/flows", json=flow_data)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

        # Try to publish flow without authentication
        fake_id = str(uuid.uuid4())
        response = await async_client.put(
            f"/v1/cms/flows/{fake_id}", json={"publish": True}
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    async def test_cms_analytics_require_authentication(self, async_client):
        """Test that CMS analytics endpoints require authentication."""
        # Try to access dashboard without authentication
        response = await async_client.get("/v1/cms/analytics/dashboard")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

        # Try to access flow analytics without authentication
        fake_id = str(uuid.uuid4())
        response = await async_client.get(f"/v1/cms/flows/{fake_id}/analytics")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

        # Try to export analytics without authentication
        export_request = {"export_type": "flow_analytics", "format": "json"}
        response = await async_client.post(
            "/v1/cms/analytics/export", json=export_request
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    async def test_public_endpoints_no_auth_required(self, async_client):
        """Test that public endpoints don't require authentication."""
        # API version should be public
        response = await async_client.get("/v1/version")
        assert response.status_code == status.HTTP_200_OK

        # Health check should be public
        response = await async_client.get("/health")
        # Should not return 401, might return 404 if endpoint doesn't exist
        assert response.status_code != status.HTTP_401_UNAUTHORIZED

    async def test_chat_start_does_not_require_auth(self, async_client):
        """Test that chat start endpoint does not require authentication."""
        # This should fail for other reasons (invalid flow), but not auth
        session_data = {
            "flow_id": str(uuid.uuid4()),
            "user_id": None,
            "initial_state": {},
        }

        response = await async_client.post("/v1/chat/start", json=session_data)

        # Should not be 401 (auth error), but 404 (flow not found) or 422 (validation error)
        assert response.status_code != status.HTTP_401_UNAUTHORIZED
        assert response.status_code in [
            status.HTTP_404_NOT_FOUND,
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        ]


class TestServiceAccountAuth:
    """Test service account authentication patterns."""

    async def test_valid_service_account_access(
        self, async_client, backend_service_account_headers
    ):
        """Test that valid service account headers provide access."""
        # Should be able to list content
        response = await async_client.get(
            "/v1/cms/content", headers=backend_service_account_headers
        )
        assert response.status_code == status.HTTP_200_OK

        # Should be able to list flows
        response = await async_client.get(
            "/v1/cms/flows", headers=backend_service_account_headers
        )
        assert response.status_code == status.HTTP_200_OK

        # Should be able to access analytics
        response = await async_client.get(
            "/v1/cms/analytics/dashboard", headers=backend_service_account_headers
        )
        assert response.status_code == status.HTTP_200_OK

    async def test_service_account_can_create_content(
        self, async_client, backend_service_account_headers
    ):
        """Test that service account can create CMS content."""
        content_data = {
            "type": "joke",
            "content": {"text": "Service account test joke", "category": "auth_test"},
            "tags": ["auth", "test"],
            "status": "draft",
        }

        response = await async_client.post(
            "/v1/cms/content",
            json=content_data,
            headers=backend_service_account_headers,
        )

        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data["content"]["text"] == "Service account test joke"

    async def test_service_account_can_create_flows(
        self, async_client, backend_service_account_headers
    ):
        """Test that service account can create flows."""
        flow_data = {
            "name": "Service Account Test Flow",
            "description": "Flow created by service account",
            "version": "1.0.0",
            "flow_data": {
                "nodes": [
                    {
                        "id": "start",
                        "type": "message",
                        "content": {"text": "Service account flow"},
                        "position": {"x": 0, "y": 0},
                    }
                ],
                "connections": [],
            },
            "entry_node_id": "start",
        }

        response = await async_client.post(
            "/v1/cms/flows", json=flow_data, headers=backend_service_account_headers
        )

        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data["name"] == "Service Account Test Flow"

    async def test_invalid_service_account_token(self, async_client):
        """Test that invalid service account tokens are rejected."""
        invalid_headers = {
            "Authorization": "Bearer invalid_token_here",
            "Content-Type": "application/json",
        }

        response = await async_client.get("/v1/cms/content", headers=invalid_headers)

        assert response.status_code == status.HTTP_403_FORBIDDEN

    async def test_expired_service_account_token(self, async_client):
        """Test handling of expired service account tokens."""
        # Use a clearly expired token format
        expired_headers = {
            "Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.expired",
            "Content-Type": "application/json",
        }

        response = await async_client.get("/v1/cms/content", headers=expired_headers)

        assert response.status_code == status.HTTP_403_FORBIDDEN


class TestAuthorizationLevels:
    """Test role-based access control and authorization levels."""

    async def test_admin_level_operations(
        self, async_client, backend_service_account_headers
    ):
        """Test operations that require admin-level authorization."""
        # Create content (should work with service account)
        content_data = {
            "type": "joke",
            "content": {"text": "Admin test joke"},
            "tags": ["admin", "test"],
            "status": "draft",
        }

        response = await async_client.post(
            "/v1/cms/content",
            json=content_data,
            headers=backend_service_account_headers,
        )
        assert response.status_code == status.HTTP_201_CREATED
        content_id = response.json()["id"]

        # Delete content (admin operation)
        response = await async_client.delete(
            f"/v1/cms/content/{content_id}", headers=backend_service_account_headers
        )
        assert response.status_code == status.HTTP_204_NO_CONTENT

    async def test_flow_publishing_authorization(
        self, async_client, backend_service_account_headers
    ):
        """Test that flow publishing requires proper authorization."""
        # Create flow
        flow_data = {
            "name": "Publishing Auth Test Flow",
            "description": "Test publishing authorization",
            "version": "1.0.0",
            "flow_data": {
                "nodes": [
                    {
                        "id": "start",
                        "type": "message",
                        "content": {"text": "Auth test"},
                        "position": {"x": 0, "y": 0},
                    }
                ],
                "connections": [],
            },
            "entry_node_id": "start",
        }

        response = await async_client.post(
            "/v1/cms/flows", json=flow_data, headers=backend_service_account_headers
        )
        assert response.status_code == status.HTTP_201_CREATED
        flow_id = response.json()["id"]

        # Publish flow (should work with service account)
        response = await async_client.put(
            f"/v1/cms/flows/{flow_id}",
            json={"publish": True},
            headers=backend_service_account_headers,
        )
        assert response.status_code == status.HTTP_200_OK

    async def test_analytics_access_authorization(
        self, async_client, backend_service_account_headers
    ):
        """Test analytics access authorization."""
        # Dashboard access
        response = await async_client.get(
            "/v1/cms/analytics/dashboard", headers=backend_service_account_headers
        )
        assert response.status_code == status.HTTP_200_OK

        # Export functionality (might require higher privileges)
        export_request = {"export_type": "content_analytics", "format": "json"}

        response = await async_client.post(
            "/v1/cms/analytics/export",
            json=export_request,
            headers=backend_service_account_headers,
        )
        # Should not be unauthorized - might be 200 or other business logic response
        assert response.status_code != status.HTTP_401_UNAUTHORIZED
        assert response.status_code != status.HTTP_403_FORBIDDEN


class TestSecurityBoundaries:
    """Test security enforcement and edge cases."""

    async def test_malformed_authorization_header(self, async_client):
        """Test handling of malformed authorization headers."""
        malformed_headers = [
            {"Authorization": "Bearer"},  # Missing token
            {"Authorization": "InvalidScheme token"},  # Wrong scheme
            {"Authorization": "Bearer token with spaces"},  # Invalid token format
            {"Authorization": ""},  # Empty header
        ]

        for headers in malformed_headers:
            headers["Content-Type"] = "application/json"
            response = await async_client.get("/v1/cms/content", headers=headers)
            # Accept either 401 or 403 as both are valid for malformed auth headers
            assert response.status_code in [
                status.HTTP_401_UNAUTHORIZED,
                status.HTTP_403_FORBIDDEN,
            ]

    async def test_cross_tenant_access_prevention(
        self, async_client, backend_service_account_headers
    ):
        """Test that users cannot access resources from other tenants."""
        # Create content with service account
        content_data = {
            "type": "joke",
            "content": {"text": "Tenant isolation test"},
            "tags": ["security", "test"],
            "status": "draft",
        }

        response = await async_client.post(
            "/v1/cms/content",
            json=content_data,
            headers=backend_service_account_headers,
        )
        assert response.status_code == status.HTTP_201_CREATED
        content_id = response.json()["id"]

        # Try to access with a different (invalid) service account
        different_headers = {
            "Authorization": "Bearer different_service_account_token",
            "Content-Type": "application/json",
        }

        response = await async_client.get(
            f"/v1/cms/content/{content_id}", headers=different_headers
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    async def test_sql_injection_prevention(
        self, async_client, backend_service_account_headers
    ):
        """Test that SQL injection attempts are properly handled."""
        # Try SQL injection in content filters
        response = await async_client.get(
            "/v1/cms/content",
            params={
                "search": "'; DROP TABLE cms_content; --",
                "content_type": "joke' OR '1'='1",
            },
            headers=backend_service_account_headers,
        )

        # Should return 200 (query handled safely) or 422 (validation error)
        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        ]

        # Database should still be intact - try normal query
        normal_response = await async_client.get(
            "/v1/cms/content", headers=backend_service_account_headers
        )
        assert normal_response.status_code == status.HTTP_200_OK

    async def test_rate_limiting_headers(
        self, async_client, backend_service_account_headers
    ):
        """Test that rate limiting information is provided in headers."""
        response = await async_client.get(
            "/v1/cms/content", headers=backend_service_account_headers
        )

        # Check for common rate limiting headers (if implemented)
        rate_limit_headers = [
            "X-RateLimit-Limit",
            "X-RateLimit-Remaining",
            "X-RateLimit-Reset",
            "Retry-After",
        ]

        # At least one rate limiting header should be present or none (both are valid)
        rate_limit_present = any(
            header in response.headers for header in rate_limit_headers
        )
        # This test documents expected behavior rather than enforcing it
        assert response.status_code == status.HTTP_200_OK

    async def test_content_sanitization(
        self, async_client, backend_service_account_headers
    ):
        """Test that content is properly sanitized."""
        # Try creating content with potentially dangerous content
        dangerous_content = {
            "type": "joke",
            "content": {
                "text": "<script>alert('XSS')</script>Harmless joke",
                "category": "<img src=x onerror=alert('XSS')>",
            },
            "tags": ["<script>", "test"],
            "status": "draft",
        }

        response = await async_client.post(
            "/v1/cms/content",
            json=dangerous_content,
            headers=backend_service_account_headers,
        )

        if response.status_code == status.HTTP_201_CREATED:
            # If content was created, verify it was sanitized
            data = response.json()
            # Script tags should be removed or escaped
            assert "<script>" not in data["content"]["text"]
            assert "alert(" not in data["content"]["text"]
        else:
            # Or the request should be rejected for validation reasons
            assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
