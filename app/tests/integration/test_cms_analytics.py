"""Tests for SQL-backed CMS flow, node, dashboard and session reporting."""

import uuid
from datetime import datetime, timedelta

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


class TestFlowAnalytics:
    """Test flow performance analytics and metrics."""

    def test_get_flow_analytics_basic(self, client, backend_service_account_headers):
        """Test basic flow analytics retrieval."""
        # First create a flow to analyze with valid nodes (required for publishing)
        flow_data = {
            "name": "Analytics Test Flow",
            "version": "1.0.0",
            "flow_data": {
                "entry_point": "start",
                "nodes": [
                    {"id": "start", "type": "message", "content": {"text": "Hello"}}
                ],
                "connections": [],
            },
            "entry_node_id": "start",
            "is_published": True,
        }

        create_response = client.post(
            "v1/cms/flows", json=flow_data, headers=backend_service_account_headers
        )
        flow_id = create_response.json()["id"]

        # Get analytics for the flow
        response = client.get(
            f"v1/cms/flows/{flow_id}/analytics",
            headers=backend_service_account_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "flow_id" in data
        assert "total_sessions" in data
        assert "completion_rate" in data
        assert "average_duration" in data
        assert "bounce_rate" in data
        assert "engagement_metrics" in data
        assert "time_period" in data

    def test_get_flow_analytics_with_date_range(
        self, client, backend_service_account_headers
    ):
        """Test flow analytics with specific date range."""
        # Create flow first
        flow_data = {
            "name": "Date Range Analytics Flow",
            "version": "1.0.0",
            "flow_data": {"entry_point": "start"},
            "entry_node_id": "start",
        }

        create_response = client.post(
            "v1/cms/flows", json=flow_data, headers=backend_service_account_headers
        )
        flow_id = create_response.json()["id"]

        # Get analytics for last 30 days
        start_date = (datetime.now() - timedelta(days=30)).date().isoformat()
        end_date = datetime.now().date().isoformat()

        response = client.get(
            f"v1/cms/flows/{flow_id}/analytics?start_date={start_date}&end_date={end_date}",
            headers=backend_service_account_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert (
            data["time_period"]["start_date"] == start_date
        )  # Now both are date strings
        assert data["time_period"]["end_date"] == end_date

    def test_get_flow_node_reach(self, client, backend_service_account_headers):
        """Test flow node reach analytics."""
        # Create flow with multiple nodes
        flow_data = {
            "name": "Node Reach Test Flow",
            "version": "1.0.0",
            "flow_data": {"entry_point": "start"},
            "entry_node_id": "start",
        }

        create_response = client.post(
            "v1/cms/flows", json=flow_data, headers=backend_service_account_headers
        )
        flow_id = create_response.json()["id"]

        # Add independently reachable nodes
        nodes = [
            {"node_id": "welcome", "node_type": "message", "content": {"messages": []}},
            {
                "node_id": "question1",
                "node_type": "question",
                "content": {"question": {}},
            },
            {
                "node_id": "question2",
                "node_type": "question",
                "content": {"question": {}},
            },
            {"node_id": "result", "node_type": "message", "content": {"messages": []}},
        ]

        for node in nodes:
            client.post(
                f"v1/cms/flows/{flow_id}/nodes",
                json=node,
                headers=backend_service_account_headers,
            )

        # Get node reach
        response = client.get(
            f"v1/cms/flows/{flow_id}/analytics/node-reach",
            headers=backend_service_account_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "flow_id" in data
        assert data["total_sessions"] == 0
        assert len(data["nodes"]) == len(nodes)
        assert all(node["reached_fraction"] is None for node in data["nodes"])
        assert "drop_off_points" not in data

    def test_get_flow_performance_over_time(
        self, client, backend_service_account_headers
    ):
        """Test flow performance metrics over time."""
        # Create flow
        flow_data = {
            "name": "Performance Tracking Flow",
            "version": "1.0.0",
            "flow_data": {"entry_point": "start"},
            "entry_node_id": "start",
        }

        create_response = client.post(
            "v1/cms/flows", json=flow_data, headers=backend_service_account_headers
        )
        flow_id = create_response.json()["id"]

        # Get performance over time (daily granularity)
        response = client.get(
            f"v1/cms/flows/{flow_id}/analytics/performance?granularity=daily&days=7",
            headers=backend_service_account_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "flow_id" in data
        assert "time_series" in data
        assert "granularity" in data
        assert data["granularity"] == "daily"
        assert isinstance(data["time_series"], list)

    def test_compare_flow_versions_analytics(
        self, client, backend_service_account_headers
    ):
        """Test comparing analytics between flow versions."""
        # Create multiple versions of a flow
        flow_v1_data = {
            "name": "Version Comparison Flow",
            "version": "1.0.0",
            "flow_data": {"entry_point": "start"},
            "entry_node_id": "start",
        }

        flow_v2_data = {
            "name": "Version Comparison Flow",
            "version": "2.0.0",
            "flow_data": {"entry_point": "start_v2"},
            "entry_node_id": "start_v2",
        }

        flow_v1_response = client.post(
            "v1/cms/flows", json=flow_v1_data, headers=backend_service_account_headers
        )
        flow_v1_id = flow_v1_response.json()["id"]

        flow_v2_response = client.post(
            "v1/cms/flows", json=flow_v2_data, headers=backend_service_account_headers
        )
        flow_v2_id = flow_v2_response.json()["id"]

        # Compare analytics between versions
        response = client.get(
            f"v1/cms/flows/analytics/compare?flow_ids={flow_v1_id},{flow_v2_id}",
            headers=backend_service_account_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "comparison" in data
        assert len(data["comparison"]) == 2
        assert "performance_delta" in data
        assert "winner" in data


class TestNodeAnalytics:
    """Test individual node performance analytics."""

    def test_get_node_engagement_metrics(self, client, backend_service_account_headers):
        """Test node-level engagement metrics."""
        # Create flow and node
        flow_data = {
            "name": "Node Analytics Flow",
            "version": "1.0.0",
            "flow_data": {"entry_point": "start"},
            "entry_node_id": "start",
        }

        create_response = client.post(
            "v1/cms/flows", json=flow_data, headers=backend_service_account_headers
        )
        flow_id = create_response.json()["id"]

        node_data = {
            "node_id": "analytics_node",
            "node_type": "question",
            "content": {
                "question": {"text": "How do you like our service?"},
                "options": ["Great", "Good", "Okay", "Poor"],
            },
        }

        node_response = client.post(
            f"v1/cms/flows/{flow_id}/nodes",
            json=node_data,
            headers=backend_service_account_headers,
        )
        node_db_id = node_response.json()["id"]

        # Get node analytics
        response = client.get(
            f"v1/cms/flows/{flow_id}/nodes/{node_db_id}/analytics",
            headers=backend_service_account_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "node_id" in data
        assert "visits" in data
        assert "interactions" in data
        assert "bounce_rate" in data
        assert data["average_time_spent"] is None
        assert "avg_response_time_seconds" not in data["response_distribution"]
        assert "response_distribution" in data


class TestAnalyticsDashboard:
    """Test analytics dashboard data and aggregations."""

    def test_get_dashboard_overview(self, client, backend_service_account_headers):
        """Test dashboard overview analytics."""
        response = client.get(
            "v1/cms/analytics/dashboard",
            headers=backend_service_account_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "overview" in data
        assert "total_flows" in data["overview"]
        assert "total_content" in data["overview"]
        assert "active_sessions" in data["overview"]
        assert "completion_rate" in data["overview"]
        assert "top_flows_by_sessions" in data
        assert "recent_activity" not in data

    def test_get_real_time_metrics(self, client, backend_service_account_headers):
        """Test real-time analytics metrics."""
        response = client.get(
            "v1/cms/analytics/real-time",
            headers=backend_service_account_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "timestamp" in data
        assert "active_sessions" in data
        assert "sessions_last_hour" in data
        assert (
            not {
                "current_interactions",
                "response_time",
                "error_rate",
                "real_time_events",
            }
            & data.keys()
        )

    def test_get_top_flows_analytics(self, client, backend_service_account_headers):
        """Test top-performing flows analytics."""
        response = client.get(
            "v1/cms/analytics/flows/top?limit=5&metric=completion_rate",
            headers=backend_service_account_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "top_flows" in data
        assert "metric" in data
        assert data["metric"] == "completion_rate"
        assert len(data["top_flows"]) <= 5


class TestAnalyticsAuthentication:
    """Test analytics endpoints require proper authentication."""

    def test_dashboard_requires_authentication(self, client):
        """Test dashboard analytics require authentication."""
        response = client.get("v1/cms/analytics/dashboard")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_flow_analytics_require_authentication(self, client):
        """Test flow analytics require authentication."""
        fake_id = str(uuid.uuid4())
        response = client.get(f"v1/cms/flows/{fake_id}/analytics")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_real_time_analytics_require_authentication(self, client):
        """Test real-time analytics require authentication."""
        response = client.get("v1/cms/analytics/real-time")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
