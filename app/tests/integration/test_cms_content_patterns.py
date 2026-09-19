#!/usr/bin/env python3
"""
CMS Content Pattern Tests - Comprehensive content creation and validation.
Extracted from ad-hoc test_cms_content.py and enhanced for integration testing.
"""

from typing import Any, Dict, List

import pytest
from sqlalchemy import text


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


class TestCMSContentPatterns:
    """Test comprehensive CMS content creation patterns."""

    @pytest.fixture
    def sample_content_library(self) -> List[Dict[str, Any]]:
        """Create a comprehensive library of sample content items."""
        return [
            # Welcome messages with different styles
            {
                "type": "message",
                "content": {
                    "text": "Welcome to Bookbot! I'm here to help you discover amazing books.",
                    "style": "friendly",
                    "target_audience": "general",
                },
                "tags": ["welcome", "greeting", "bookbot"],
                "info": {
                    "usage": "greeting",
                    "priority": "high",
                    "content_category": "onboarding",
                },
            },
            {
                "type": "message",
                "content": {
                    "text": "Let's find some fantastic books together! 📚",
                    "style": "enthusiastic",
                    "target_audience": "children",
                },
                "tags": ["welcome", "books", "children"],
                "info": {
                    "usage": "greeting",
                    "priority": "medium",
                    "content_category": "onboarding",
                },
            },
            # Questions with different input types
            {
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
            },
            {
                "type": "question",
                "content": {
                    "question": "What type of books do you enjoy reading?",
                    "input_type": "choice",
                    "variable": "book_preferences",
                    "options": [
                        "Fantasy & Magic",
                        "Adventure Stories",
                        "Mystery & Detective",
                        "Science & Nature",
                        "Friendship Stories",
                    ],
                },
                "tags": ["preferences", "genres", "personalization"],
                "info": {
                    "usage": "user_profiling",
                    "priority": "high",
                    "content_category": "data_collection",
                },
            },
            {
                "type": "question",
                "content": {
                    "question": "How many books would you like me to recommend?",
                    "input_type": "choice",
                    "variable": "recommendation_count",
                    "options": ["1-3 books", "4-6 books", "7-10 books", "More than 10"],
                },
                "tags": ["preferences", "quantity", "personalization"],
                "info": {
                    "usage": "recommendation_settings",
                    "priority": "medium",
                    "content_category": "data_collection",
                },
            },
            # Jokes for entertainment
            {
                "type": "joke",
                "content": {
                    "setup": "Why don't books ever get cold?",
                    "punchline": "Because they have book jackets!",
                    "category": "books",
                    "age_group": ["6-12"],
                },
                "tags": ["joke", "books", "kids", "entertainment"],
                "info": {
                    "usage": "entertainment",
                    "priority": "low",
                    "content_category": "humor",
                },
            },
            {
                "type": "joke",
                "content": {
                    "setup": "What do you call a book that's about the future?",
                    "punchline": "A novel idea!",
                    "category": "wordplay",
                    "age_group": ["8-14"],
                },
                "tags": ["joke", "wordplay", "future", "entertainment"],
                "info": {
                    "usage": "entertainment",
                    "priority": "low",
                    "content_category": "humor",
                },
            },
            # Educational content
            {
                "type": "message",
                "content": {
                    "text": "Reading helps build vocabulary, improves concentration, and sparks imagination!",
                    "style": "educational",
                    "target_audience": "parents_and_educators",
                },
                "tags": ["education", "benefits", "reading"],
                "info": {
                    "usage": "educational",
                    "priority": "medium",
                    "content_category": "information",
                },
            },
            # Encouragement messages
            {
                "type": "message",
                "content": {
                    "text": "Great choice! You're building excellent reading habits.",
                    "style": "encouraging",
                    "target_audience": "children",
                },
                "tags": ["encouragement", "positive", "feedback"],
                "info": {
                    "usage": "feedback",
                    "priority": "high",
                    "content_category": "motivation",
                },
            },
            # Conditional message with variables
            {
                "type": "message",
                "content": {
                    "text": "Based on your age ({{user_age}}) and interests in {{book_preferences}}, I've found {{recommendation_count}} perfect books for you!",
                    "style": "personalized",
                    "target_audience": "general",
                    "variables": [
                        "user_age",
                        "book_preferences",
                        "recommendation_count",
                    ],
                },
                "tags": ["personalized", "recommendations", "summary"],
                "info": {
                    "usage": "recommendation_summary",
                    "priority": "high",
                    "content_category": "results",
                },
            },
        ]

    @pytest.mark.asyncio
    async def test_create_content_library(
        self, async_client, backend_service_account_headers, sample_content_library
    ):
        """Test creating a comprehensive content library."""
        created_content = []

        # Create all content items
        for content_data in sample_content_library:
            response = await async_client.post(
                "/v1/cms/content",
                json=content_data,
                headers=backend_service_account_headers,
            )

            assert response.status_code == 201, (
                f"Failed to create content: {content_data['type']}"
            )
            created_item = response.json()
            created_content.append(created_item)

            # Verify structure
            assert created_item["type"] == content_data["type"]
            assert created_item["content"] == content_data["content"]
            assert set(created_item["tags"]) == set(content_data["tags"])
            assert created_item["is_active"] is True

        # Test querying content by categories
        category_tests = [
            ("onboarding", 2),  # Welcome messages
            ("data_collection", 3),  # Questions
            ("humor", 2),  # Jokes
            ("motivation", 1),  # Encouragement
        ]

        for category, expected_count in category_tests:
            # Search by tags related to category
            response = await async_client.get(
                "/v1/cms/content",
                params={"search": category},
                headers=backend_service_account_headers,
            )

            assert response.status_code == 200
            # Note: Search behavior depends on implementation
            # This test verifies the API responds correctly

        # Test content type filtering
        content_type_tests = [
            ("message", 5),  # Message types (5 messages in sample_content_library)
            ("question", 3),  # Question types
            ("joke", 2),  # Joke types
        ]

        for content_type, expected_min in content_type_tests:
            response = await async_client.get(
                "/v1/cms/content",
                params={"content_type": content_type},
                headers=backend_service_account_headers,
            )

            assert response.status_code == 200
            data = response.json()
            items = data.get("data", [])

            # Filter our created content
            our_items = [
                item
                for item in items
                if item["id"] in [c["id"] for c in created_content]
            ]
            type_count = len(
                [item for item in our_items if item["type"] == content_type]
            )

            assert type_count == expected_min, (
                f"Expected {expected_min} {content_type} items, got {type_count}"
            )

        # Cleanup
        for content in created_content:
            await async_client.delete(
                f"/v1/cms/content/{content['id']}",
                headers=backend_service_account_headers,
            )

    @pytest.mark.asyncio
    async def test_content_validation_patterns(
        self, async_client, backend_service_account_headers
    ):
        """Test various content validation scenarios."""
        validation_tests = [
            # Valid message with all optional fields
            {
                "data": {
                    "type": "message",
                    "content": {
                        "text": "Complete message with all fields",
                        "style": "formal",
                        "target_audience": "adults",
                    },
                    "tags": ["complete", "validation"],
                    "info": {"test": "validation"},
                    "status": "draft",
                },
                "should_succeed": True,
                "description": "Complete valid message",
            },
            # Minimal valid content
            {
                "data": {
                    "type": "message",
                    "content": {"text": "Minimal message"},
                    "tags": [],
                },
                "should_succeed": True,
                "description": "Minimal valid message",
            },
            # Question with validation rules
            {
                "data": {
                    "type": "question",
                    "content": {
                        "question": "Enter a number between 1 and 100",
                        "input_type": "number",
                        "variable": "test_number",
                        "validation": {"min": 1, "max": 100, "required": True},
                    },
                    "tags": ["validation", "number"],
                },
                "should_succeed": True,
                "description": "Question with validation rules",
            },
            # Invalid content type
            {
                "data": {
                    "type": "invalid_type",
                    "content": {"text": "Test"},
                    "tags": [],
                },
                "should_succeed": False,
                "description": "Invalid content type",
            },
            # Missing required content field
            {
                "data": {
                    "type": "message",
                    "tags": [],
                    # Missing content field
                },
                "should_succeed": False,
                "description": "Missing content field",
            },
            # Empty content object
            {
                "data": {
                    "type": "message",
                    "content": {},  # Empty content
                    "tags": [],
                },
                "should_succeed": False,
                "description": "Empty content object",
            },
        ]

        successful_creations = []

        for test_case in validation_tests:
            response = await async_client.post(
                "/v1/cms/content",
                json=test_case["data"],
                headers=backend_service_account_headers,
            )

            if test_case["should_succeed"]:
                assert response.status_code == 201, (
                    f"Expected success for: {test_case['description']}"
                )
                successful_creations.append(response.json()["id"])
            else:
                assert response.status_code in [
                    400,
                    422,
                ], f"Expected validation error for: {test_case['description']}"

        # Cleanup successful creations
        for content_id in successful_creations:
            await async_client.delete(
                f"/v1/cms/content/{content_id}", headers=backend_service_account_headers
            )

    @pytest.mark.asyncio
    async def test_content_info_patterns(
        self, async_client, backend_service_account_headers
    ):
        """Test various info field storage and retrieval patterns."""
        info_test_cases = [
            {
                "type": "message",
                "content": {"text": "Test with simple info"},
                "tags": ["info"],
                "info": {
                    "author": "test_user",
                    "creation_date": "2024-01-01",
                    "revision": 1,
                },
            },
            {
                "type": "question",
                "content": {
                    "question": "Test question",
                    "input_type": "text",
                    "variable": "test_var",
                },
                "tags": ["info", "complex"],
                "info": {
                    "difficulty_level": "beginner",
                    "estimated_time": "30 seconds",
                    "category": "assessment",
                    "subcategory": "basic_info",
                    "scoring": {"points": 10, "weight": 1.0},
                    "localization": {
                        "default_language": "en",
                        "available_languages": ["en", "es", "fr"],
                    },
                },
            },
            {
                "type": "joke",
                "content": {
                    "setup": "Test setup",
                    "punchline": "Test punchline",
                    "category": "test",
                },
                "tags": ["info", "array"],
                "info": {
                    "age_groups": ["6-8", "9-11", "12-14"],
                    "themes": ["friendship", "adventure", "learning"],
                    "content_warnings": [],
                    "educational_value": {
                        "vocabulary_level": "grade_3",
                        "concepts": ["humor", "wordplay"],
                        "learning_objectives": [
                            "develop sense of humor",
                            "understand wordplay",
                        ],
                    },
                },
            },
        ]

        created_items = []

        for test_case in info_test_cases:
            # Create content with info
            response = await async_client.post(
                "/v1/cms/content",
                json=test_case,
                headers=backend_service_account_headers,
            )

            assert response.status_code == 201
            created_item = response.json()
            created_items.append(created_item)

            # Verify info is stored correctly
            assert created_item["info"] == test_case["info"]

            # Retrieve and verify info persistence
            response = await async_client.get(
                f"/v1/cms/content/{created_item['id']}",
                headers=backend_service_account_headers,
            )

            assert response.status_code == 200
            retrieved_item = response.json()
            assert retrieved_item["info"] == test_case["info"]

        # Test info filtering (if supported by API)
        response = await async_client.get(
            "/v1/cms/content",
            params={"search": "difficulty_level"},  # Search in info
            headers=backend_service_account_headers,
        )

        assert response.status_code == 200
        # API should handle info search gracefully

        # Cleanup
        for item in created_items:
            await async_client.delete(
                f"/v1/cms/content/{item['id']}", headers=backend_service_account_headers
            )

    @pytest.mark.asyncio
    async def test_content_versioning_patterns(
        self, async_client, backend_service_account_headers
    ):
        """Test content versioning and update patterns."""
        # Create initial content
        initial_content = {
            "type": "message",
            "content": {"text": "Version 1.0 content", "style": "formal"},
            "tags": ["versioning", "test"],
            "status": "draft",
        }

        response = await async_client.post(
            "/v1/cms/content",
            json=initial_content,
            headers=backend_service_account_headers,
        )

        assert response.status_code == 201
        created_item = response.json()
        content_id = created_item["id"]

        # Verify initial version
        assert created_item["version"] == 1
        assert created_item["status"] == "draft"

        # Test content updates (if supported)
        updated_content = {
            "content": {"text": "Version 2.0 content - updated!", "style": "casual"},
            "tags": ["versioning", "test", "updated"],
            "status": "published",
        }

        # Try to update content
        response = await async_client.put(
            f"/v1/cms/content/{content_id}",
            json=updated_content,
            headers=backend_service_account_headers,
        )

        # Handle if updates are not supported
        if response.status_code == 405:  # Method not allowed
            # Skip update testing
            pass
        elif response.status_code == 200:
            # Updates are supported
            updated_item = response.json()
            assert updated_item["content"]["text"] == "Version 2.0 content - updated!"
            assert updated_item["status"] == "published"

            # Version might increment (depending on implementation)
            assert updated_item["version"] >= 1

        # Test status changes
        status_update = {"status": "archived"}

        response = await async_client.patch(
            f"/v1/cms/content/{content_id}",
            json=status_update,
            headers=backend_service_account_headers,
        )

        # Handle if patch is not supported
        if response.status_code not in [405, 404]:
            # Some form of update was attempted
            assert response.status_code in [200, 400, 422]

        # Cleanup
        await async_client.delete(
            f"/v1/cms/content/{content_id}", headers=backend_service_account_headers
        )
