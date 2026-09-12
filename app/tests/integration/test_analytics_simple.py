"""
Simple Analytics Test - Just test the basic functionality first.
"""

import uuid

import pytest
from sqlalchemy import text
from starlette import status

from app.models.cms import FlowDefinition


# Test isolation fixture for CMS data
@pytest.fixture(autouse=True)
async def cleanup_cms_data(async_session):
    """Clean up CMS data before and after each test to ensure test isolation."""
    cms_tables = [
        "conversation_history",
        "conversation_sessions",
        "flow_connections",
        "flow_nodes",
        "flow_definitions",
        "cms_content_variants",
        "cms_content",
    ]

    # Clean up before test runs
    for table in cms_tables:
        try:
            await async_session.execute(
                text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE")
            )
        except Exception:
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
            pass
    await async_session.commit()


@pytest.fixture
async def simple_flow(async_session):
    """Create a simple flow for testing."""
    flow = FlowDefinition(
        id=uuid.uuid4(),
        name="Simple Test Flow",
        description="A simple flow for testing",
        version="1.0.0",
        flow_data={"nodes": [], "connections": []},
        entry_node_id="start",
        is_active=True,
        is_published=True,
    )
    async_session.add(flow)
    await async_session.commit()
    return flow


class TestSimpleAnalytics:
    """Test basic analytics functionality."""

    async def test_flow_analytics_basic(
        self, async_client, backend_service_account_headers, simple_flow
    ):
        """Test basic flow analytics endpoint."""
        flow_id = str(simple_flow.id)

        response = await async_client.get(
            f"/v1/cms/flows/{flow_id}/analytics",
            headers=backend_service_account_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        analytics = response.json()

        # Should return basic structure even with no data
        assert "flow_id" in analytics
        assert "total_sessions" in analytics
        assert "completion_rate" in analytics
        assert analytics["flow_id"] == flow_id

    async def test_dashboard_without_data(
        self, async_client, backend_service_account_headers
    ):
        """Test dashboard with no flow data."""
        response = await async_client.get(
            "/v1/cms/analytics/dashboard", headers=backend_service_account_headers
        )

        assert response.status_code == status.HTTP_200_OK
        dashboard = response.json()

        # Should have basic structure
        assert "overview" in dashboard
        assert "top_flows_by_sessions" in dashboard
        assert "recent_activity" not in dashboard
