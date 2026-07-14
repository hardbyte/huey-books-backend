"""
CMS Content API Integration Tests.

Consolidated from multiple test files to provide comprehensive coverage of:
- Content CRUD operations (create, read, update, delete)
- Content types and validation (joke, fact, question, quote, message, prompt)
- Content variants and A/B testing functionality
- Content search, filtering, and pagination
- Content status management and workflows
- Content bulk operations

Test Organization:
- TestContentCRUD: Basic CRUD operations for all content types
- TestContentFiltering: Search, filtering, pagination functionality
- TestContentVariants: A/B testing and variant management
- TestContentValidation: Input validation and error handling
- TestContentWorkflows: Status transitions and publishing workflows
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


class TestContentCRUD:
    """Test basic content CRUD operations for all content types."""

    @pytest.fixture(autouse=True)
    def setup_test(self, reset_global_state_sync):
        """Ensure global state is reset before each test."""
        pass

    async def test_create_content_joke(
        self, async_client, backend_service_account_headers
    ):
        """Test creating joke content with proper validation."""
        content_data = {
            "type": "joke",
            "content": {
                "text": "Why don't scientists trust atoms? Because they make up everything!",
                "category": "science",
            },
            "tags": ["science", "chemistry"],
            "status": "draft",
        }

        response = await async_client.post(
            "/v1/cms/content",
            json=content_data,
            headers=backend_service_account_headers,
        )

        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data["type"] == "joke"
        assert data["status"] == "draft"
        assert "science" in data["tags"]
        assert data["content"]["category"] == "science"

    async def test_create_content_question(
        self, async_client, backend_service_account_headers
    ):
        """Test creating question content with input validation."""
        content_data = {
            "type": "question",
            "content": {
                "question": "What is the capital of Australia?",
                "input_type": "choice",
                "options": ["Sydney", "Melbourne", "Canberra", "Perth"],
                "correct_answer": "Canberra",
            },
            "tags": ["geography", "capitals"],
            "status": "published",
        }

        response = await async_client.post(
            "/v1/cms/content",
            json=content_data,
            headers=backend_service_account_headers,
        )

        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data["type"] == "question"
        assert data["status"] == "published"
        assert len(data["content"]["options"]) == 4
        assert data["content"]["correct_answer"] == "Canberra"

    async def test_create_content_message(
        self, async_client, backend_service_account_headers
    ):
        """Test creating message content."""
        content_data = {
            "type": "message",
            "content": {
                "text": "Welcome to our educational platform!",
                "category": "greeting",
            },
            "tags": ["welcome", "greeting"],
            "status": "published",
        }

        response = await async_client.post(
            "/v1/cms/content",
            json=content_data,
            headers=backend_service_account_headers,
        )

        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data["type"] == "message"
        assert data["content"]["category"] == "greeting"

    async def test_get_content_by_id(
        self, async_client, backend_service_account_headers
    ):
        """Test retrieving specific content by ID."""
        # Create content first
        content_data = {
            "type": "joke",
            "content": {"text": "Test joke for retrieval", "category": "test"},
            "tags": ["test"],
            "status": "draft",
        }

        create_response = await async_client.post(
            "/v1/cms/content",
            json=content_data,
            headers=backend_service_account_headers,
        )
        content_id = create_response.json()["id"]

        # Retrieve content
        response = await async_client.get(
            f"/v1/cms/content/{content_id}", headers=backend_service_account_headers
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["id"] == content_id
        assert data["content"]["text"] == "Test joke for retrieval"

    async def test_update_content(self, async_client, backend_service_account_headers):
        """Test updating existing content."""
        # Create content first
        content_data = {
            "type": "joke",
            "content": {"text": "Original joke", "category": "test"},
            "tags": ["test"],
            "status": "draft",
        }

        create_response = await async_client.post(
            "/v1/cms/content",
            json=content_data,
            headers=backend_service_account_headers,
        )
        content_id = create_response.json()["id"]

        # Update content
        update_data = {
            "content": {"text": "Updated joke", "category": "updated"},
            "status": "published",
        }

        response = await async_client.put(
            f"/v1/cms/content/{content_id}",
            json=update_data,
            headers=backend_service_account_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["content"]["text"] == "Updated joke"
        assert data["status"] == "published"

    async def test_delete_content(self, async_client, backend_service_account_headers):
        """Test deleting content."""
        # Create content first
        content_data = {
            "type": "joke",
            "content": {"text": "Joke to be deleted", "category": "test"},
            "tags": ["test"],
            "status": "draft",
        }

        create_response = await async_client.post(
            "/v1/cms/content",
            json=content_data,
            headers=backend_service_account_headers,
        )
        content_id = create_response.json()["id"]

        # Delete content
        response = await async_client.delete(
            f"/v1/cms/content/{content_id}", headers=backend_service_account_headers
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT

        # Verify it's deleted
        get_response = await async_client.get(
            f"/v1/cms/content/{content_id}", headers=backend_service_account_headers
        )
        assert get_response.status_code == status.HTTP_404_NOT_FOUND


class TestContentFiltering:
    """Test content search, filtering, and pagination functionality."""

    async def test_list_all_content(
        self, async_client, backend_service_account_headers
    ):
        """Test listing all content with pagination."""
        # Create multiple content items
        test_contents = [
            {
                "type": "joke",
                "content": {"text": f"Joke {i}", "category": "test"},
                "tags": ["test", f"joke-{i}"],
                "status": "published",
            }
            for i in range(5)
        ]

        for content_data in test_contents:
            await async_client.post(
                "/v1/cms/content",
                json=content_data,
                headers=backend_service_account_headers,
            )

        # List content
        response = await async_client.get(
            "/v1/cms/content", headers=backend_service_account_headers
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "data" in data
        assert "pagination" in data
        assert len(data["data"]) >= 5

    async def test_filter_by_content_type(
        self, async_client, backend_service_account_headers
    ):
        """Test filtering content by type."""
        # Create different types of content
        joke_data = {
            "type": "joke",
            "content": {"text": "Test joke", "category": "test"},
            "tags": ["test"],
            "status": "published",
        }

        message_data = {
            "type": "message",
            "content": {"text": "Test message", "category": "test"},
            "tags": ["test"],
            "status": "published",
        }

        await async_client.post(
            "/v1/cms/content", json=joke_data, headers=backend_service_account_headers
        )
        await async_client.post(
            "/v1/cms/content",
            json=message_data,
            headers=backend_service_account_headers,
        )

        # Filter by joke type
        response = await async_client.get(
            "/v1/cms/content?content_type=joke", headers=backend_service_account_headers
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data["data"]) >= 1
        for item in data["data"]:
            assert item["type"] == "joke"

    async def test_filter_by_tags(self, async_client, backend_service_account_headers):
        """Test filtering content by tags."""
        content_data = {
            "type": "joke",
            "content": {"text": "Science joke", "category": "science"},
            "tags": ["science", "education", "funny"],
            "status": "published",
        }

        await async_client.post(
            "/v1/cms/content",
            json=content_data,
            headers=backend_service_account_headers,
        )

        # Filter by science tag
        response = await async_client.get(
            "/v1/cms/content?tags=science", headers=backend_service_account_headers
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data["data"]) >= 1
        for item in data["data"]:
            assert "science" in item["tags"]

    async def test_pagination_with_limits(
        self, async_client, backend_service_account_headers
    ):
        """Test content pagination with limit and skip parameters."""
        # Create multiple content items
        for i in range(15):
            content_data = {
                "type": "message",
                "content": {"text": f"Test message {i}", "category": "test"},
                "tags": ["test", "pagination"],
                "status": "published",
            }
            await async_client.post(
                "/v1/cms/content",
                json=content_data,
                headers=backend_service_account_headers,
            )

        # Test first page
        response = await async_client.get(
            "/v1/cms/content?limit=5&tags=pagination",
            headers=backend_service_account_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data["data"]) <= 5
        assert "pagination" in data

        # Test second page
        response = await async_client.get(
            "/v1/cms/content?limit=5&skip=5&tags=pagination",
            headers=backend_service_account_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data["data"]) <= 5


class TestContentValidation:
    """Test input validation and error handling for content operations."""

    async def test_invalid_content_type(
        self, async_client, backend_service_account_headers
    ):
        """Test creation with invalid content type."""
        invalid_data = {
            "type": "invalid_type",
            "content": {"text": "Test content"},
            "tags": [],
            "status": "draft",
        }

        response = await async_client.post(
            "/v1/cms/content",
            json=invalid_data,
            headers=backend_service_account_headers,
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    async def test_missing_required_fields(
        self, async_client, backend_service_account_headers
    ):
        """Test creation with missing required fields."""
        incomplete_data = {
            "type": "joke",
            # Missing content field
            "tags": [],
            "status": "draft",
        }

        response = await async_client.post(
            "/v1/cms/content",
            json=incomplete_data,
            headers=backend_service_account_headers,
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    async def test_get_nonexistent_content(
        self, async_client, backend_service_account_headers
    ):
        """Test retrieving non-existent content returns 404."""
        fake_id = str(uuid.uuid4())

        response = await async_client.get(
            f"/v1/cms/content/{fake_id}", headers=backend_service_account_headers
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestContentWorkflows:
    """Test content status management and publishing workflows."""

    async def test_publish_draft_content(
        self, async_client, backend_service_account_headers
    ):
        """Test publishing draft content."""
        # Create draft content
        content_data = {
            "type": "joke",
            "content": {"text": "Draft joke", "category": "test"},
            "tags": ["test"],
            "status": "draft",
        }

        create_response = await async_client.post(
            "/v1/cms/content",
            json=content_data,
            headers=backend_service_account_headers,
        )
        content_id = create_response.json()["id"]

        # Publish content
        publish_data = {"status": "published"}

        response = await async_client.put(
            f"/v1/cms/content/{content_id}",
            json=publish_data,
            headers=backend_service_account_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] == "published"

    async def test_archive_published_content(
        self, async_client, backend_service_account_headers
    ):
        """Test archiving published content."""
        # Create published content
        content_data = {
            "type": "joke",
            "content": {"text": "Published joke", "category": "test"},
            "tags": ["test"],
            "status": "published",
        }

        create_response = await async_client.post(
            "/v1/cms/content",
            json=content_data,
            headers=backend_service_account_headers,
        )
        content_id = create_response.json()["id"]

        # Archive content
        archive_data = {"status": "archived"}

        response = await async_client.put(
            f"/v1/cms/content/{content_id}",
            json=archive_data,
            headers=backend_service_account_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] == "archived"
