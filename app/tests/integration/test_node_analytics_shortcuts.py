"""
Test to expose and fix node analytics shortcuts.
This test will fail with the current shortcut implementation and pass with proper fixes.
"""

import json

import pytest
from sqlalchemy import text
from starlette import status


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


class TestNodeAnalyticsShortcuts:
    """Test that exposes shortcuts in node analytics implementation."""

    def test_hardcoded_average_time_shortcut(
        self, client, backend_service_account_headers
    ):
        """
        Test that unavailable node timing is not fabricated as 30 seconds.
        """

        # Create a flow with a test node
        flow_data = {
            "name": "Time Calculation Test Flow",
            "version": "1.0.0",
            "flow_data": {"entry_point": "test_node"},
            "entry_node_id": "test_node",
        }

        flow_response = client.post(
            "v1/cms/flows", json=flow_data, headers=backend_service_account_headers
        )
        assert flow_response.status_code == status.HTTP_201_CREATED
        flow_id = flow_response.json()["id"]

        # Create a node
        node_data = {
            "node_id": "test_node",
            "node_type": "message",
            "content": {"messages": [{"text": "Test message"}]},
        }

        node_response = client.post(
            f"v1/cms/flows/{flow_id}/nodes",
            json=node_data,
            headers=backend_service_account_headers,
        )
        assert node_response.status_code == status.HTTP_201_CREATED

        # Get analytics for empty node (no conversation data)
        analytics_response = client.get(
            f"v1/cms/flows/{flow_id}/nodes/test_node/analytics",
            headers=backend_service_account_headers,
        )

        assert analytics_response.status_code == status.HTTP_200_OK
        data = analytics_response.json()

        print(f"Analytics data: {json.dumps(data, indent=2)}")

        # CRITICAL TEST: If this returns exactly 30.0 with no data, it's hardcoded
        average_time = data.get("average_time_spent")
        print(f"Average time spent: {average_time}")

        # This test SHOULD FAIL with the current shortcut implementation
        # because it returns 30.0 even when there's no conversation data
        if average_time == 30.0:
            pytest.fail(
                "average_time_spent is hardcoded to 30.0 - this is a shortcut that needs fixing. "
                "With no conversation data, it should be 0.0"
            )

    def test_oversimplified_response_distribution_shortcut(
        self, client, backend_service_account_headers
    ):
        """
        Test that response_distribution analyzes actual ConversationHistory.content JSON,
        not just basic math between visits and interactions.
        """

        # Create flow and node
        flow_data = {
            "name": "Response Distribution Test Flow",
            "version": "1.0.0",
            "flow_data": {"entry_point": "question_node"},
            "entry_node_id": "question_node",
        }

        flow_response = client.post(
            "v1/cms/flows", json=flow_data, headers=backend_service_account_headers
        )
        assert flow_response.status_code == status.HTTP_201_CREATED
        flow_id = flow_response.json()["id"]

        # Create a question node with specific options
        node_data = {
            "node_id": "question_node",
            "node_type": "question",
            "content": {
                "question": {"text": "Choose your favorite color?"},
                "input_type": "buttons",
                "options": [
                    {"text": "Red", "value": "red"},
                    {"text": "Blue", "value": "blue"},
                    {"text": "Green", "value": "green"},
                ],
            },
        }

        node_response = client.post(
            f"v1/cms/flows/{flow_id}/nodes",
            json=node_data,
            headers=backend_service_account_headers,
        )
        assert node_response.status_code == status.HTTP_201_CREATED

        # Get analytics
        analytics_response = client.get(
            f"v1/cms/flows/{flow_id}/nodes/question_node/analytics",
            headers=backend_service_account_headers,
        )

        assert analytics_response.status_code == status.HTTP_200_OK
        data = analytics_response.json()

        response_dist = data.get("response_distribution", {})
        print(f"Response distribution: {json.dumps(response_dist, indent=2)}")

        # CRITICAL TEST: The current shortcut just does:
        # {
        #   'text_responses': interactions,
        #   'button_clicks': max(0, visits - interactions)
        # }
        #
        # This is a clear shortcut that doesn't analyze actual response content
        shortcut_keys = {"text_responses", "button_clicks"}
        actual_keys = set(response_dist.keys())

        # This test SHOULD FAIL with current shortcut implementation
        if shortcut_keys == actual_keys:
            pytest.fail(
                f"response_distribution only contains generic shortcut keys {shortcut_keys}. "
                "This indicates oversimplified implementation that doesn't analyze actual response content. "
                "Should contain specific response values or proper analysis of ConversationHistory.content"
            )

    def test_proper_zero_handling_with_no_data(
        self, client, backend_service_account_headers
    ):
        """Test that analytics handle empty data correctly without shortcuts."""

        flow_data = {
            "name": "Empty Analytics Test Flow",
            "version": "1.0.0",
            "flow_data": {"entry_point": "empty_node"},
            "entry_node_id": "empty_node",
        }

        flow_response = client.post(
            "v1/cms/flows", json=flow_data, headers=backend_service_account_headers
        )
        assert flow_response.status_code == status.HTTP_201_CREATED
        flow_id = flow_response.json()["id"]

        node_data = {
            "node_id": "empty_node",
            "node_type": "message",
            "content": {"messages": [{"text": "Empty test"}]},
        }

        node_response = client.post(
            f"v1/cms/flows/{flow_id}/nodes",
            json=node_data,
            headers=backend_service_account_headers,
        )
        assert node_response.status_code == status.HTTP_201_CREATED

        # Get analytics for node with no conversation data
        analytics_response = client.get(
            f"v1/cms/flows/{flow_id}/nodes/empty_node/analytics",
            headers=backend_service_account_headers,
        )

        assert analytics_response.status_code == status.HTTP_200_OK
        data = analytics_response.json()

        print(f"Empty node analytics: {json.dumps(data, indent=2)}")

        # With no data, proper implementation should return 0s, not hardcoded values
        assert data.get("visits") == 0
        assert data.get("interactions") == 0

        assert data["average_time_spent"] is None
        assert data.get("bounce_rate") == 0.0
