"""
Integration tests for CMS and Chat APIs with proper authentication.
"""

import logging
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.models import ServiceAccount, ServiceAccountType
from app.services.security import create_access_token

# Set up verbose logging for debugging test setup failures
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


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


@pytest.fixture
async def backend_service_account(async_session):
    """Create a backend service account for testing."""
    logger.info("Creating backend service account for CMS integration test")

    try:
        service_account = ServiceAccount(
            name=f"test-backend-{uuid4()}",
            type=ServiceAccountType.BACKEND,
            is_active=True,
        )
        logger.debug(f"Created service account object: {service_account.name}")

        async_session.add(service_account)
        logger.debug("Added service account to session")

        await async_session.commit()
        logger.debug("Committed service account to database")

        await async_session.refresh(service_account)
        logger.info(
            f"Successfully created service account with ID: {service_account.id}"
        )

        return service_account
    except Exception as e:
        logger.error(f"Failed to create backend service account: {e}")
        raise


@pytest.fixture
async def backend_auth_token(backend_service_account):
    """Create a JWT token for backend service account."""
    logger.info(
        f"Creating auth token for service account: {backend_service_account.id}"
    )

    try:
        token = create_access_token(
            subject=f"wriveted:service-account:{backend_service_account.id}",
            expires_delta=None,
        )
        logger.debug("Successfully created JWT token")
        return token
    except Exception as e:
        logger.error(f"Failed to create auth token: {e}")
        raise


@pytest.fixture
async def auth_headers(backend_auth_token):
    """Create authorization headers."""
    logger.info("Creating authorization headers")
    headers = {"Authorization": f"Bearer {backend_auth_token}"}
    logger.debug(
        f"Created headers with Bearer token (length: {len(backend_auth_token)})"
    )
    return headers


class TestCMSContentAPI:
    """Test CMS Content management with authentication."""

    @pytest.mark.asyncio
    async def test_create_cms_content_joke(self, async_client, auth_headers):
        """Test creating a joke content item."""
        logger.info("Starting test_create_cms_content_joke")

        try:
            logger.debug("Verifying fixtures are available...")
            assert async_client is not None, "async_client fixture not available"
            assert auth_headers is not None, "auth_headers fixture not available"
            logger.debug("All fixtures verified successfully")

            joke_data = {
                "type": "joke",
                "content": {
                    "text": "Why do programmers prefer dark mode? Because light attracts bugs!",
                    "category": "programming",
                    "audience": "developers",
                },
                "status": "published",
                "tags": ["programming", "humor", "developers"],
                "info": {"source": "pytest_test", "difficulty": "easy", "rating": 4.2},
            }
            logger.debug(f"Created test data: {joke_data}")

            logger.info("Making POST request to /cms/content")
            response = await async_client.post(
                "/v1/cms/content", json=joke_data, headers=auth_headers
            )
            logger.debug(f"Received response with status: {response.status_code}")
        except Exception as e:
            logger.error(f"Error in test_create_cms_content_joke: {e}")
            logger.exception("Full traceback:")
            raise

        assert response.status_code == 201
        data = response.json()
        assert data["type"] == "joke"
        assert data["status"] == "published"
        assert "programming" in data["tags"]
        assert data["info"]["source"] == "pytest_test"
        assert "id" in data

        return data["id"]

    @pytest.mark.asyncio
    async def test_create_cms_content_question(self, async_client, auth_headers):
        """Test creating a question content item."""
        question_data = {
            "type": "question",
            "content": {
                "text": "What programming language would you like to learn next?",
                "options": ["Python", "JavaScript", "Rust", "Go", "TypeScript"],
                "response_type": "single_choice",
                "allow_other": True,
            },
            "status": "published",
            "tags": ["programming", "learning", "survey"],
            "info": {"purpose": "skill_assessment", "weight": 1.5},
        }

        response = await async_client.post(
            "/v1/cms/content", json=question_data, headers=auth_headers
        )

        assert response.status_code == 201
        data = response.json()
        assert data["type"] == "question"
        assert data["content"]["allow_other"] is True
        assert len(data["content"]["options"]) == 5

        return data["id"]

    @pytest.mark.asyncio
    async def test_create_cms_content_message(self, async_client, auth_headers):
        """Test creating a message content item."""
        message_data = {
            "type": "message",
            "content": {
                "text": "Welcome to our interactive coding challenge! Let's start with something fun.",
                "tone": "encouraging",
                "context": "challenge_intro",
            },
            "status": "published",
            "tags": ["welcome", "coding", "challenge"],
            "info": {"template_version": "3.1", "localization_ready": True},
        }

        response = await async_client.post(
            "/v1/cms/content", json=message_data, headers=auth_headers
        )

        assert response.status_code == 201
        data = response.json()
        assert data["type"] == "message"
        assert data["content"]["tone"] == "encouraging"
        assert data["info"]["localization_ready"] is True

        return data["id"]

    @pytest.mark.asyncio
    async def test_list_cms_content(self, async_client, auth_headers):
        """Test listing all CMS content."""
        # First create some content
        await self.test_create_cms_content_joke(async_client, auth_headers)
        await self.test_create_cms_content_question(async_client, auth_headers)

        response = await async_client.get("/v1/cms/content", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert "pagination" in data
        assert len(data["data"]) >= 2

        # Check that we have different content types
        content_types = {item["type"] for item in data["data"]}
        assert "joke" in content_types or "question" in content_types

    @pytest.mark.asyncio
    async def test_filter_cms_content_by_type(self, async_client, auth_headers):
        """Test filtering CMS content by type."""
        # Create a joke
        await self.test_create_cms_content_joke(async_client, auth_headers)

        # Filter by JOKE type
        response = await async_client.get(
            "/v1/cms/content?content_type=JOKE", headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]) >= 1

        # All returned items should be jokes
        for item in data["data"]:
            assert item["type"] == "joke"

    @pytest.mark.asyncio
    async def test_get_specific_cms_content(self, async_client, auth_headers):
        """Test getting a specific content item by ID."""
        # Create content first
        content_id = await self.test_create_cms_content_message(
            async_client, auth_headers
        )

        response = await async_client.get(
            f"/v1/cms/content/{content_id}", headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == content_id
        assert data["type"] == "message"
        assert data["content"]["tone"] == "encouraging"

    @pytest.mark.asyncio
    async def test_update_cms_content(self, async_client, auth_headers):
        """Test updating CMS content."""
        # Create content first
        content_id = await self.test_create_cms_content_joke(async_client, auth_headers)

        update_data = {
            "content": {
                "text": "Why do programmers prefer dark mode? Because light attracts bugs! (Updated)",
                "category": "programming",
                "audience": "all_developers",
            },
            "tags": ["programming", "humor", "developers", "updated"],
        }

        response = await async_client.put(
            f"/v1/cms/content/{content_id}", json=update_data, headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        assert "(Updated)" in data["content"]["text"]
        assert "updated" in data["tags"]
        assert data["content"]["audience"] == "all_developers"


class TestCMSFlowAPI:
    """Test CMS Flow management with authentication."""

    @pytest.mark.asyncio
    async def test_create_flow_definition(self, async_client, auth_headers):
        """Test creating a complete flow definition."""
        flow_data = {
            "name": "Programming Skills Assessment Flow",
            "description": "A comprehensive flow to assess programming skills and provide recommendations",
            "version": "2.1",
            "flow_data": {
                "nodes": [
                    {
                        "id": "welcome",
                        "type": "message",
                        "content": {
                            "text": "Welcome to our programming skills assessment! This will help us understand your experience level."
                        },
                        "position": {"x": 100, "y": 100},
                    },
                    {
                        "id": "ask_experience",
                        "type": "question",
                        "content": {
                            "text": "How many years of programming experience do you have?",
                            "options": [
                                "Less than 1 year",
                                "1-3 years",
                                "3-5 years",
                                "5+ years",
                            ],
                            "variable": "experience_level",
                        },
                        "position": {"x": 100, "y": 200},
                    },
                    {
                        "id": "ask_languages",
                        "type": "question",
                        "content": {
                            "text": "Which programming languages are you comfortable with?",
                            "options": [
                                "Python",
                                "JavaScript",
                                "Java",
                                "C++",
                                "Go",
                                "Rust",
                            ],
                            "variable": "known_languages",
                            "multiple": True,
                        },
                        "position": {"x": 100, "y": 300},
                    },
                    {
                        "id": "generate_assessment",
                        "type": "ACTION",
                        "content": {
                            "action_type": "skill_assessment",
                            "params": {
                                "experience": "{experience_level}",
                                "languages": "{known_languages}",
                            },
                        },
                        "position": {"x": 100, "y": 400},
                    },
                    {
                        "id": "show_results",
                        "type": "message",
                        "content": {
                            "text": "Based on your {experience_level} experience with {known_languages}, here's your personalized learning path!"
                        },
                        "position": {"x": 100, "y": 500},
                    },
                ],
                "connections": [
                    {
                        "source": "welcome",
                        "target": "ask_experience",
                        "type": "DEFAULT",
                    },
                    {
                        "source": "ask_experience",
                        "target": "ask_languages",
                        "type": "DEFAULT",
                    },
                    {
                        "source": "ask_languages",
                        "target": "generate_assessment",
                        "type": "DEFAULT",
                    },
                    {
                        "source": "generate_assessment",
                        "target": "show_results",
                        "type": "DEFAULT",
                    },
                ],
            },
            "entry_node_id": "welcome",
            "info": {
                "author": "pytest_integration_test",
                "category": "assessment",
                "estimated_duration": "4-6 minutes",
                "skill_level": "all",
            },
            "is_published": True,
            "is_active": True,
        }

        response = await async_client.post(
            "/v1/cms/flows", json=flow_data, headers=auth_headers
        )

        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Programming Skills Assessment Flow"
        # Version auto-increments when is_published=True: 2.1 -> 2.2.0
        assert data["version"] == "2.2.0"
        assert data["is_published"] is True
        assert data["is_active"] is True
        assert len(data["flow_data"]["nodes"]) == 5
        assert len(data["flow_data"]["connections"]) == 4
        assert data["entry_node_id"] == "welcome"

        return data["id"]

    @pytest.mark.asyncio
    async def test_list_flows(self, async_client, auth_headers):
        """Test listing all flows."""
        # Create a flow first
        await self.test_create_flow_definition(async_client, auth_headers)

        response = await async_client.get("/v1/cms/flows", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert "pagination" in data
        assert len(data["data"]) >= 1

        # Check that at least one flow is from our test
        flow_names = [flow["name"] for flow in data["data"]]
        assert "Programming Skills Assessment Flow" in flow_names

    @pytest.mark.asyncio
    async def test_get_specific_flow(self, async_client, auth_headers):
        """Test getting a specific flow by ID."""
        # Create flow first
        flow_id = await self.test_create_flow_definition(async_client, auth_headers)

        response = await async_client.get(
            f"/v1/cms/flows/{flow_id}", headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == flow_id
        assert data["name"] == "Programming Skills Assessment Flow"
        assert len(data["flow_data"]["nodes"]) == 5

    @pytest.mark.asyncio
    async def test_get_flow_nodes(self, async_client, auth_headers):
        """Test getting nodes for a specific flow."""
        # Create flow first
        flow_id = await self.test_create_flow_definition(async_client, auth_headers)

        response = await async_client.get(
            f"/v1/cms/flows/{flow_id}/nodes", headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert len(data["data"]) == 5

        # Check that we have the expected node types
        node_types = {node["node_type"] for node in data["data"]}
        assert "message" in node_types
        assert "question" in node_types
        assert "action" in node_types

    @pytest.mark.asyncio
    async def test_get_flow_connections(self, async_client, auth_headers):
        """Test getting connections for a specific flow."""
        # Create flow first
        flow_id = await self.test_create_flow_definition(async_client, auth_headers)

        response = await async_client.get(
            f"/v1/cms/flows/{flow_id}/connections", headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert len(data["data"]) == 4

        # Check connection structure
        for connection in data["data"]:
            assert "source_node_id" in connection
            assert "target_node_id" in connection
            assert "connection_type" in connection


class TestChatAPI:
    """Test Chat API functionality."""

    @pytest.mark.asyncio
    async def test_start_chat_session_with_published_flow(
        self, async_client, auth_headers
    ):
        """Test starting a chat session with a published flow."""
        # Create a flow first
        flow_test = TestCMSFlowAPI()
        flow_id = await flow_test.test_create_flow_definition(
            async_client, auth_headers
        )

        # Publish the flow so it can be used for chat
        publish_response = await async_client.put(
            f"/v1/cms/flows/{flow_id}",
            json={"publish": True},
            headers=auth_headers,
        )
        assert publish_response.status_code == 200

        session_data = {
            "flow_id": flow_id,
            "user_id": None,
            "initial_state": {"test_mode": True, "source": "pytest"},
        }

        response = await async_client.post("/v1/chat/start", json=session_data)

        assert response.status_code == 201
        data = response.json()
        assert "session_id" in data
        assert "session_token" in data
        assert data["session_id"] is not None
        assert data["session_token"] is not None

        return data["session_token"]

    @pytest.mark.asyncio
    async def test_get_session_state(self, async_client, auth_headers):
        """Test getting session state."""
        # Start a session first
        session_token = await self.test_start_chat_session_with_published_flow(
            async_client, auth_headers
        )

        response = await async_client.get(f"/v1/chat/sessions/{session_token}")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "active"
        assert "state" in data
        assert data["state"]["test_mode"] is True
        assert data["state"]["source"] == "pytest"

    @pytest.mark.asyncio
    async def test_chat_session_with_unpublished_flow_fails(
        self, async_client, auth_headers
    ):
        """Test that unpublished flows cannot be used for chat sessions."""
        # Create an unpublished flow
        flow_data = {
            "name": "Unpublished Test Flow",
            "description": "This flow should not be accessible for chat",
            "version": "1.0",
            "flow_data": {"nodes": [], "connections": []},
            "entry_node_id": "start",
            "is_published": False,  # Explicitly unpublished
            "is_active": True,
        }

        flow_response = await async_client.post(
            "/v1/cms/flows", json=flow_data, headers=auth_headers
        )
        assert flow_response.status_code == 201
        flow_id = flow_response.json()["id"]

        # Try to start a session with the unpublished flow
        session_data = {"flow_id": flow_id, "user_id": None, "initial_state": {}}

        response = await async_client.post("/v1/chat/start", json=session_data)

        assert response.status_code == 404
        assert (
            "not found" in response.json()["detail"].lower()
            or "not available" in response.json()["detail"].lower()
        )


class TestCMSAuthentication:
    """Test CMS authentication requirements."""

    @pytest.mark.asyncio
    async def test_cms_content_requires_auth(self, async_client):
        """Test that CMS content endpoints require authentication."""
        # Try to access CMS content without auth
        response = await async_client.get("/v1/cms/content")
        assert response.status_code == 401

        # Try to create content without auth
        response = await async_client.post("/v1/cms/content", json={"type": "joke"})
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_cms_flows_requires_auth(self, async_client):
        """Test that CMS flow endpoints require authentication."""
        # Try to access flows without auth
        response = await async_client.get("/v1/cms/flows")
        assert response.status_code == 401

        # Try to create flow without auth
        response = await async_client.post("/v1/cms/flows", json={"name": "Test"})
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_chat_start_does_not_require_auth(self, async_client):
        """Test that chat start endpoint does not require authentication."""
        # This should fail for other reasons (invalid flow), but not auth
        response = await async_client.post(
            "/chat/start",
            json={"flow_id": str(uuid4()), "user_id": None, "initial_state": {}},
        )

        # Should not be 401 (auth error), but 404 (flow not found)
        assert response.status_code != 401
        assert response.status_code == 404


class TestCMSIntegrationWorkflow:
    """Test complete CMS workflow integration."""

    @pytest.mark.asyncio
    async def test_complete_cms_to_chat_workflow(self, async_client, auth_headers):
        """Test a self-contained, isolated workflow from CMS content creation to chat session."""
        created_content_ids = []
        created_flow_id = None

        try:
            # 1. Create CMS content
            content_to_create = [
                {
                    "type": "joke",
                    "content": {"text": "A joke for the isolated workflow"},
                    "tags": ["isolated_test"],
                },
                {
                    "type": "question",
                    "content": {"text": "A question for the isolated workflow"},
                    "tags": ["isolated_test"],
                },
            ]

            for content_data in content_to_create:
                response = await async_client.post(
                    "/v1/cms/content", json=content_data, headers=auth_headers
                )
                assert response.status_code == 201
                created_content_ids.append(response.json()["id"])

            # 2. Create a flow with valid nodes (required for publishing)
            flow_data = {
                "name": "Isolated Test Flow",
                "version": "1.0",
                "is_published": True,
                "flow_data": {
                    "nodes": [
                        {
                            "id": "start",
                            "type": "message",
                            "content": {"text": "Welcome to the test flow"},
                        }
                    ],
                    "connections": [],
                },
                "entry_node_id": "start",
            }
            response = await async_client.post(
                "/v1/cms/flows", json=flow_data, headers=auth_headers
            )
            assert response.status_code == 201
            created_flow_id = response.json()["id"]

            # 3. Start a chat session with the created flow
            session_data = {"flow_id": created_flow_id}
            response = await async_client.post("/v1/chat/start", json=session_data)
            assert response.status_code == 201
            session_token = response.json()["session_token"]

            # 4. Verify session is working
            response = await async_client.get(f"/v1/chat/sessions/{session_token}")
            assert response.status_code == 200
            assert response.json()["status"] == "active"

        finally:
            # 5. Cleanup all created resources
            for content_id in created_content_ids:
                await async_client.delete(
                    f"/v1/cms/content/{content_id}", headers=auth_headers
                )

            if created_flow_id:
                await async_client.delete(
                    f"/v1/cms/flows/{created_flow_id}", headers=auth_headers
                )
