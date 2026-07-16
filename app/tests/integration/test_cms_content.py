"""
Comprehensive CMS Content Management Tests.

This module consolidates all content-related tests from multiple CMS test files:
- Content CRUD operations (create, read, update, delete)
- Content types and validation (joke, fact, question, quote, message, prompt)
- Content variants and A/B testing functionality
- Content search, filtering, and pagination
- Content status management and workflows
- Content bulk operations

Consolidated from:
- test_cms.py (content CRUD and variants)
- test_cms_api_enhanced.py (filtering and creation patterns)
- test_cms_authenticated.py (authenticated content operations)
- test_cms_content_patterns.py (content library and validation patterns)
- test_cms_full_integration.py (content API integration tests)
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


class TestContentCRUD:
    """Test basic content CRUD operations."""

    @pytest.fixture(autouse=True)
    def setup_test(self, reset_global_state_sync):
        """Ensure global state is reset before each test in this class."""
        pass

    def test_create_content_joke(self, client, backend_service_account_headers):
        """Test creating new joke content."""
        content_data = {
            "type": "joke",
            "content": {
                "text": "Why don't scientists trust atoms? Because they make up everything!",
                "category": "science",
            },
            "tags": ["science", "chemistry"],
            "status": "draft",
        }

        response = client.post(
            "v1/cms/content", json=content_data, headers=backend_service_account_headers
        )

        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data["type"] == "joke"
        assert data["content"]["text"] == content_data["content"]["text"]
        assert data["tags"] == content_data["tags"]
        assert data["status"] == "draft"
        assert data["version"] == 1
        assert data["is_active"] is True
        assert "id" in data
        assert "created_at" in data

    def test_create_content_fact(self, client, backend_service_account_headers):
        """Test creating fact content with source information."""
        content_data = {
            "type": "fact",
            "content": {
                "text": "Octopuses have three hearts.",
                "source": "Marine Biology Facts",
                "difficulty": "intermediate",
            },
            "tags": ["animals", "ocean", "biology"],
            "status": "published",
            "info": {"verification_status": "verified", "last_updated": "2024-01-15"},
        }

        response = client.post(
            "v1/cms/content", json=content_data, headers=backend_service_account_headers
        )

        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data["type"] == "fact"
        assert data["content"]["source"] == "Marine Biology Facts"
        assert data["status"] == "published"
        assert data["info"]["verification_status"] == "verified"

    def test_create_content_question(self, client, backend_service_account_headers):
        """Test creating question content with input validation."""
        content_data = {
            "type": "question",
            "content": {
                "question": "What's your age? This helps me recommend the perfect books for you!",
                "input_type": "number",
                "variable": "user_age",
                "validation": {"min": 3, "max": 18, "required": True},
            },
            "tags": ["age", "onboarding", "personalization"],
            "info": {
                "usage": "user_profiling",
                "priority": "high",
                "content_category": "data_collection",
            },
        }

        response = client.post(
            "v1/cms/content", json=content_data, headers=backend_service_account_headers
        )

        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data["type"] == "question"
        assert data["content"]["input_type"] == "number"
        assert data["content"]["validation"]["min"] == 3
        assert data["info"]["usage"] == "user_profiling"

    def test_create_content_message(self, client, backend_service_account_headers):
        """Test creating message content with rich formatting."""
        content_data = {
            "type": "message",
            "content": {
                "text": "Welcome to Bookbot! I'm here to help you discover amazing books.",
                "style": "friendly",
                "target_audience": "general",
                "typing_delay": 1.5,
                "media": {
                    "type": "image",
                    "url": "https://example.com/bookbot.gif",
                    "alt": "Bookbot waving",
                },
            },
            "tags": ["welcome", "greeting", "bookbot"],
            "info": {
                "usage": "greeting",
                "priority": "high",
                "content_category": "onboarding",
            },
        }

        response = client.post(
            "v1/cms/content", json=content_data, headers=backend_service_account_headers
        )

        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data["type"] == "message"
        assert data["content"]["style"] == "friendly"
        assert data["content"]["media"]["type"] == "image"

    def test_create_content_quote(self, client, backend_service_account_headers):
        """Test creating quote content with attribution."""
        content_data = {
            "type": "quote",
            "content": {
                "text": "The only way to do great work is to love what you do.",
                "author": "Steve Jobs",
                "context": "Stanford commencement address",
                "theme": "motivation",
            },
            "tags": ["motivation", "career", "inspiration"],
            "status": "published",
        }

        response = client.post(
            "v1/cms/content", json=content_data, headers=backend_service_account_headers
        )

        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data["type"] == "quote"
        assert data["content"]["author"] == "Steve Jobs"
        assert data["content"]["theme"] == "motivation"

    def test_create_content_prompt(self, client, backend_service_account_headers):
        """Test creating prompt content for AI interactions."""
        content_data = {
            "type": "prompt",
            "content": {
                "system_prompt": "You are a helpful reading assistant for children",
                "user_prompt": "Recommend 3 books for a {age}-year-old who likes {genre}",
                "parameters": ["age", "genre"],
                "model_config": {"temperature": 0.7, "max_tokens": 500},
            },
            "tags": ["ai", "recommendations", "books"],
            "info": {"model_version": "gpt-4", "usage_context": "book_recommendations"},
        }

        response = client.post(
            "v1/cms/content", json=content_data, headers=backend_service_account_headers
        )

        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data["type"] == "prompt"
        assert "system_prompt" in data["content"]
        assert len(data["content"]["parameters"]) == 2

    def test_get_content_by_id(self, client, backend_service_account_headers):
        """Test retrieving specific content by ID."""
        # First create content
        content_data = {
            "type": "fact",
            "content": {
                "text": "Octopuses have three hearts.",
                "source": "Marine Biology Facts",
            },
            "tags": ["animals", "ocean"],
        }

        create_response = client.post(
            "v1/cms/content", json=content_data, headers=backend_service_account_headers
        )
        content_id = create_response.json()["id"]

        # Get the content
        response = client.get(
            f"v1/cms/content/{content_id}", headers=backend_service_account_headers
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["id"] == content_id
        assert data["type"] == "fact"
        assert data["content"]["text"] == content_data["content"]["text"]

    def test_get_nonexistent_content(self, client, backend_service_account_headers):
        """Test retrieving non-existent content returns 404."""
        fake_id = str(uuid.uuid4())
        response = client.get(
            f"v1/cms/content/{fake_id}", headers=backend_service_account_headers
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_update_content(self, client, backend_service_account_headers):
        """Test updating existing content."""
        # Create content first
        content_data = {
            "type": "quote",
            "content": {
                "text": "The only way to do great work is to love what you do.",
                "author": "Steve Jobs",
            },
            "tags": ["motivation"],
            "status": "draft",
        }

        create_response = client.post(
            "v1/cms/content", json=content_data, headers=backend_service_account_headers
        )
        content_id = create_response.json()["id"]

        # Update the content
        update_data = {
            "content": {
                "text": "The only way to do great work is to love what you do.",
                "author": "Steve Jobs",
                "context": "Stanford commencement address, 2005",
            },
            "tags": ["motivation", "career", "education"],
            "status": "published",
        }

        response = client.put(
            f"v1/cms/content/{content_id}",
            json=update_data,
            headers=backend_service_account_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["content"]["context"] == "Stanford commencement address, 2005"
        assert "education" in data["tags"]
        assert data["status"] == "published"
        assert data["version"] == 2  # Version should increment

    def test_update_nonexistent_content(self, client, backend_service_account_headers):
        """Test updating non-existent content returns 404."""
        fake_id = str(uuid.uuid4())
        update_data = {"content": {"text": "Updated text"}}

        response = client.put(
            f"v1/cms/content/{fake_id}",
            json=update_data,
            headers=backend_service_account_headers,
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_delete_content(self, client, backend_service_account_headers):
        """Test soft deletion of content."""
        # Create content first
        content_data = {
            "type": "joke",
            "content": {"text": "Content to be deleted"},
            "tags": ["test"],
        }

        create_response = client.post(
            "v1/cms/content", json=content_data, headers=backend_service_account_headers
        )
        content_id = create_response.json()["id"]

        # Delete the content
        response = client.delete(
            f"v1/cms/content/{content_id}", headers=backend_service_account_headers
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT

        # Verify content is deleted (should return 404)
        get_response = client.get(
            f"v1/cms/content/{content_id}", headers=backend_service_account_headers
        )
        assert get_response.status_code == status.HTTP_404_NOT_FOUND

    def test_delete_nonexistent_content(self, client, backend_service_account_headers):
        """Test deleting non-existent content returns 404."""
        fake_id = str(uuid.uuid4())
        response = client.delete(
            f"v1/cms/content/{fake_id}", headers=backend_service_account_headers
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestContentListing:
    """Test content listing, filtering, and search functionality."""

    def test_list_all_content(self, client, backend_service_account_headers):
        """Test listing all content with pagination."""
        response = client.get("v1/cms/content", headers=backend_service_account_headers)

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "data" in data
        assert "pagination" in data
        assert isinstance(data["data"], list)

    def test_list_content_by_type_joke(self, client, backend_service_account_headers):
        """Test filtering content by joke type."""
        response = client.get(
            "v1/cms/content?content_type=joke", headers=backend_service_account_headers
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        for item in data["data"]:
            assert item["type"] == "joke"

    def test_list_content_by_type_question(
        self, client, backend_service_account_headers
    ):
        """Test filtering content by question type."""
        response = client.get(
            "v1/cms/content?content_type=question",
            headers=backend_service_account_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        for item in data["data"]:
            assert item["type"] == "question"

    def test_filter_content_by_status(self, client, backend_service_account_headers):
        """Test filtering content by status."""
        # Test published content
        response = client.get(
            "v1/cms/content?status=published", headers=backend_service_account_headers
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        for item in data["data"]:
            assert item["status"] == "published"

        # Test draft content
        response = client.get(
            "v1/cms/content?status=draft", headers=backend_service_account_headers
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        for item in data["data"]:
            assert item["status"] == "draft"

    def test_filter_content_by_tags(self, client, backend_service_account_headers):
        """Test filtering content by tags."""
        response = client.get(
            "v1/cms/content?tags=science", headers=backend_service_account_headers
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        for item in data["data"]:
            assert "science" in item["tags"]

    def test_search_content(self, client, backend_service_account_headers):
        """Test text search functionality."""
        response = client.get(
            "v1/cms/content?search=science", headers=backend_service_account_headers
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        # Each item should contain "science" in content, tags, or metadata
        for item in data["data"]:
            text_content = str(item["content"]).lower()
            tags_content = " ".join(item["tags"]).lower()
            search_text = f"{text_content} {tags_content}"
            assert "science" in search_text

    def test_pagination_limits(self, client, backend_service_account_headers):
        """Test pagination with different limits."""
        # Test limit=1
        response = client.get(
            "v1/cms/content?limit=1", headers=backend_service_account_headers
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data["data"]) <= 1
        assert data["pagination"]["limit"] == 1

        # Test limit=5
        response = client.get(
            "v1/cms/content?limit=5", headers=backend_service_account_headers
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data["data"]) <= 5
        assert data["pagination"]["limit"] == 5

    def test_combined_filters(self, client, backend_service_account_headers):
        """Test combining multiple filters."""
        response = client.get(
            "v1/cms/content?content_type=joke&status=published&tags=science",
            headers=backend_service_account_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        for item in data["data"]:
            assert item["type"] == "joke"
            assert item["status"] == "published"
            assert "science" in item["tags"]


class TestContentVariants:
    """Test content variants and A/B testing functionality."""

    def test_create_content_variant(self, client, backend_service_account_headers):
        """Test creating a variant of existing content."""
        # First create base content
        content_data = {
            "type": "joke",
            "content": {
                "text": "Why don't scientists trust atoms? Because they make up everything!",
                "category": "science",
            },
            "tags": ["science"],
        }

        create_response = client.post(
            "v1/cms/content", json=content_data, headers=backend_service_account_headers
        )
        content_id = create_response.json()["id"]

        # Create a variant
        variant_data = {
            "variant_key": "enthusiastic",
            "variant_data": {
                "text": "Why don't scientists trust atoms? Because they make up EVERYTHING! 🧪⚛️",
                "category": "science",
                "tone": "enthusiastic",
            },
            "weight": 30,
            "conditions": {"age_group": ["7-10"], "engagement_level": "high"},
        }

        response = client.post(
            f"v1/cms/content/{content_id}/variants",
            json=variant_data,
            headers=backend_service_account_headers,
        )

        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data["variant_key"] == "enthusiastic"
        assert data["weight"] == 30
        assert "🧪⚛️" in data["variant_data"]["text"]
        assert data["conditions"]["age_group"] == ["7-10"]

    def test_list_content_variants(self, client, backend_service_account_headers):
        """Test listing all variants for a content item."""
        # First create content with variant (using previous test logic)
        content_data = {
            "type": "message",
            "content": {"text": "Welcome message"},
            "tags": ["welcome"],
        }

        create_response = client.post(
            "v1/cms/content", json=content_data, headers=backend_service_account_headers
        )
        content_id = create_response.json()["id"]

        # Create multiple variants
        variants = [
            {
                "variant_key": "formal",
                "variant_data": {"text": "Good day! Welcome to our platform."},
            },
            {
                "variant_key": "casual",
                "variant_data": {"text": "Hey there! Welcome aboard!"},
            },
        ]

        for variant in variants:
            client.post(
                f"v1/cms/content/{content_id}/variants",
                json=variant,
                headers=backend_service_account_headers,
            )

        # List variants
        response = client.get(
            f"v1/cms/content/{content_id}/variants",
            headers=backend_service_account_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data["data"]) == 2
        variant_keys = [v["variant_key"] for v in data["data"]]
        assert "formal" in variant_keys
        assert "casual" in variant_keys

    def test_update_variant_performance(self, client, backend_service_account_headers):
        """Test updating variant performance metrics."""
        # Create content and variant
        content_data = {"type": "fact", "content": {"text": "Test fact"}}
        create_response = client.post(
            "v1/cms/content", json=content_data, headers=backend_service_account_headers
        )
        content_id = create_response.json()["id"]

        variant_data = {
            "variant_key": "test_variant",
            "variant_data": {"text": "Enhanced test fact"},
        }
        variant_response = client.post(
            f"v1/cms/content/{content_id}/variants",
            json=variant_data,
            headers=backend_service_account_headers,
        )
        variant_id = variant_response.json()["id"]

        # Update performance data
        performance_data = {
            "performance_data": {
                "impressions": 100,
                "clicks": 15,
                "conversion_rate": 0.15,
                "engagement_score": 4.2,
            }
        }

        response = client.patch(
            f"v1/cms/content/{content_id}/variants/{variant_id}",
            json=performance_data,
            headers=backend_service_account_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["performance_data"]["impressions"] == 100
        assert data["performance_data"]["conversion_rate"] == 0.15

    def test_delete_content_variant(self, client, backend_service_account_headers):
        """Test deleting a content variant."""
        # Create content and variant
        content_data = {"type": "quote", "content": {"text": "Test quote"}}
        create_response = client.post(
            "v1/cms/content", json=content_data, headers=backend_service_account_headers
        )
        content_id = create_response.json()["id"]

        variant_data = {
            "variant_key": "to_delete",
            "variant_data": {"text": "Variant to delete"},
        }
        variant_response = client.post(
            f"v1/cms/content/{content_id}/variants",
            json=variant_data,
            headers=backend_service_account_headers,
        )
        variant_id = variant_response.json()["id"]

        # Delete the variant
        response = client.delete(
            f"v1/cms/content/{content_id}/variants/{variant_id}",
            headers=backend_service_account_headers,
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT

        # Verify variant is deleted
        list_response = client.get(
            f"v1/cms/content/{content_id}/variants",
            headers=backend_service_account_headers,
        )
        data = list_response.json()
        variant_ids = [v["id"] for v in data["data"]]
        assert variant_id not in variant_ids


class TestContentValidation:
    """Test content validation and error handling."""

    def test_create_content_invalid_type(self, client, backend_service_account_headers):
        """Test creating content with invalid type."""
        content_data = {
            "type": "invalid_type",
            "content": {"text": "Test content"},
        }

        response = client.post(
            "v1/cms/content", json=content_data, headers=backend_service_account_headers
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    def test_create_content_missing_content(
        self, client, backend_service_account_headers
    ):
        """Test creating content without content field."""
        content_data = {
            "type": "joke",
            "tags": ["test"],
        }

        response = client.post(
            "v1/cms/content", json=content_data, headers=backend_service_account_headers
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    def test_create_content_empty_content(
        self, client, backend_service_account_headers
    ):
        """Test creating content with empty content field."""
        content_data = {
            "type": "message",
            "content": {},
        }

        response = client.post(
            "v1/cms/content", json=content_data, headers=backend_service_account_headers
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    def test_update_content_invalid_status(
        self, client, backend_service_account_headers
    ):
        """Test updating content with invalid status."""
        # Create content first
        content_data = {"type": "fact", "content": {"text": "Test fact"}}
        create_response = client.post(
            "v1/cms/content", json=content_data, headers=backend_service_account_headers
        )
        content_id = create_response.json()["id"]

        # Try to update with invalid status
        update_data = {"status": "invalid_status"}
        response = client.put(
            f"v1/cms/content/{content_id}",
            json=update_data,
            headers=backend_service_account_headers,
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


class TestContentBulkOperations:
    """Test bulk content operations."""

    def test_bulk_update_content_status(self, client, backend_service_account_headers):
        """Test bulk updating content status."""
        # Create multiple content items
        content_items = []
        for i in range(3):
            content_data = {
                "type": "fact",
                "content": {"text": f"Test fact {i}"},
                "status": "draft",
            }
            response = client.post(
                "v1/cms/content",
                json=content_data,
                headers=backend_service_account_headers,
            )
            content_items.append(response.json()["id"])

        # Bulk update status to published
        bulk_update_data = {
            "content_ids": content_items,
            "updates": {"status": "published"},
        }

        response = client.patch(
            "v1/cms/content/bulk",
            json=bulk_update_data,
            headers=backend_service_account_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["updated_count"] == len(content_items)

        # Verify all items are published
        for content_id in content_items:
            get_response = client.get(
                f"v1/cms/content/{content_id}", headers=backend_service_account_headers
            )
            assert get_response.json()["status"] == "published"

    def test_bulk_delete_content(self, client, backend_service_account_headers):
        """Test bulk deleting content."""
        # Create multiple content items
        content_items = []
        for i in range(2):
            content_data = {"type": "joke", "content": {"text": f"Test joke {i}"}}
            response = client.post(
                "v1/cms/content",
                json=content_data,
                headers=backend_service_account_headers,
            )
            content_items.append(response.json()["id"])

        # Bulk delete
        bulk_delete_data = {"content_ids": content_items}

        response = client.request(
            "DELETE",
            "v1/cms/content/bulk",
            json=bulk_delete_data,
            headers=backend_service_account_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["deleted_count"] == len(content_items)

        # Verify all items are soft deleted
        for content_id in content_items:
            get_response = client.get(
                f"v1/cms/content/{content_id}", headers=backend_service_account_headers
            )
            assert get_response.status_code == status.HTTP_404_NOT_FOUND


class TestContentAuthentication:
    """Test content operations require proper authentication."""

    def test_list_content_requires_authentication(self, client):
        """Test that listing content requires authentication."""
        response = client.get("v1/cms/content")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_create_content_requires_authentication(self, client):
        """Test that creating content requires authentication."""
        content_data = {"type": "joke", "content": {"text": "Test joke"}}
        response = client.post("v1/cms/content", json=content_data)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_update_content_requires_authentication(self, client):
        """Test that updating content requires authentication."""
        fake_id = str(uuid.uuid4())
        update_data = {"content": {"text": "Updated text"}}
        response = client.put(f"v1/cms/content/{fake_id}", json=update_data)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_delete_content_requires_authentication(self, client):
        """Test that deleting content requires authentication."""
        fake_id = str(uuid.uuid4())
        response = client.delete(f"v1/cms/content/{fake_id}")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
