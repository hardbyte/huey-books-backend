"""
Unit tests for Analytics Service with mocked repository layer.

These tests demonstrate service layer unit testing patterns by:
1. Mocking the repository layer dependencies
2. Testing business logic in isolation
3. Verifying service behavior without database dependencies
4. Testing error handling and edge cases
"""

from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.schemas.analytics import FlowAnalytics, NodeAnalytics
from app.services.analytics import AnalyticsService


class TestAnalyticsService:
    """Unit tests for Analytics Service business logic."""

    @pytest.fixture
    def mock_db(self):
        """Mock database session."""
        return AsyncMock()

    @pytest.fixture
    def analytics_service(self):
        """Analytics service instance."""
        return AnalyticsService()

    async def test_get_flow_analytics_success(self, analytics_service, mock_db):
        """Test successful flow analytics generation."""
        flow_id = "test-flow-123"

        # Mock the internal repository methods
        with patch.object(
            analytics_service,
            "_get_session_metrics",
            new=AsyncMock(
                return_value={
                    "total_sessions": 10,
                    "completed_sessions": 8,
                    "unique_users": 7,
                    "avg_duration_seconds": 120.0,
                }
            ),
        ):
            with patch.object(
                analytics_service,
                "_get_interaction_metrics",
                new=AsyncMock(return_value={"total_interactions": 25}),
            ):
                # Act
                result = await analytics_service.get_flow_analytics(
                    db=mock_db, flow_id=flow_id
                )

                # Assert
                assert isinstance(result, FlowAnalytics)
                assert result.flow_id == flow_id
                assert result.total_sessions == 10
                assert result.completion_rate == 0.8  # 8/10
                assert result.average_duration == 2.0  # 120 seconds / 60
                assert abs(result.bounce_rate - 0.2) < 0.01  # 1 - completion_rate
                assert result.engagement_metrics["total_interactions"] == 25

    async def test_get_node_analytics_success(self, analytics_service, mock_db):
        """Test successful node analytics generation."""
        flow_id = "test-flow-123"
        node_id = "test-node-456"

        # Mock the node metrics repository call
        with patch.object(
            analytics_service,
            "_get_node_metrics",
            new=AsyncMock(
                return_value={
                    "views": 50,
                    "interactions": 35,
                }
            ),
        ):
            # Act
            result = await analytics_service.get_node_analytics(
                db=mock_db, flow_id=flow_id, node_id=node_id
            )

            # Assert
            assert isinstance(result, NodeAnalytics)
            assert result.node_id == node_id
            assert result.visits == 50
            assert result.interactions == 35
            assert result.average_time_spent is None
            assert "avg_response_time_seconds" not in result.response_distribution
            assert abs(result.bounce_rate - 0.3) < 0.01  # 1 - (35/50)

    async def test_get_flow_analytics_with_date_range(self, analytics_service, mock_db):
        """Test flow analytics with custom date range."""
        flow_id = "test-flow-123"
        start_date = date.today() - timedelta(days=14)
        end_date = date.today() - timedelta(days=7)

        with patch.object(
            analytics_service,
            "_get_session_metrics",
            new=AsyncMock(
                return_value={
                    "total_sessions": 5,
                    "completed_sessions": 3,
                    "unique_users": 4,
                    "avg_duration_seconds": 90.0,
                }
            ),
        ):
            with patch.object(
                analytics_service,
                "_get_interaction_metrics",
                new=AsyncMock(return_value={"total_interactions": 12}),
            ):
                # Act
                result = await analytics_service.get_flow_analytics(
                    db=mock_db,
                    flow_id=flow_id,
                    start_date=start_date,
                    end_date=end_date,
                )

                # Assert - verify date range is properly set
                assert result.time_period["start_date"] == start_date.isoformat()
                assert result.time_period["end_date"] == end_date.isoformat()
                assert result.time_period["days"] == 7

    async def test_get_flow_analytics_empty_data(self, analytics_service, mock_db):
        """Test flow analytics with no data."""
        flow_id = "empty-flow"

        with patch.object(
            analytics_service,
            "_get_session_metrics",
            new=AsyncMock(
                return_value={
                    "total_sessions": 0,
                    "completed_sessions": 0,
                    "unique_users": 0,
                    "avg_duration_seconds": None,
                }
            ),
        ):
            with patch.object(
                analytics_service,
                "_get_interaction_metrics",
                new=AsyncMock(return_value={"total_interactions": 0}),
            ):
                # Act
                result = await analytics_service.get_flow_analytics(
                    db=mock_db, flow_id=flow_id
                )

                # Assert - should handle empty data gracefully
                assert result.total_sessions == 0
                assert result.completion_rate == 0.0
                assert result.average_duration == 0.0
                assert result.bounce_rate == 1.0

    async def test_get_node_analytics_no_interactions(self, analytics_service, mock_db):
        """Test node analytics with no interactions."""
        flow_id = "test-flow-123"
        node_id = "unused-node"

        with patch.object(
            analytics_service,
            "_get_node_metrics",
            new=AsyncMock(
                return_value={
                    "views": 0,
                    "interactions": 0,
                }
            ),
        ):
            # Act
            result = await analytics_service.get_node_analytics(
                db=mock_db, flow_id=flow_id, node_id=node_id
            )

            # Assert - should handle no data gracefully
            assert result.visits == 0
            assert result.interactions == 0
            assert result.bounce_rate == 0.0  # No views, so no bounce

    async def test_service_layer_isolation(self, analytics_service, mock_db):
        """Test that service layer doesn't have direct database dependencies."""
        flow_id = "isolation-test"

        # The service should work with mocked repository calls
        with patch.object(
            analytics_service,
            "_get_session_metrics",
            new=AsyncMock(
                return_value={
                    "total_sessions": 1,
                    "completed_sessions": 1,
                    "unique_users": 1,
                    "avg_duration_seconds": 60.0,
                }
            ),
        ):
            with patch.object(
                analytics_service,
                "_get_interaction_metrics",
                new=AsyncMock(return_value={"total_interactions": 3}),
            ):
                # This should work without any real database connection
                result = await analytics_service.get_flow_analytics(
                    db=mock_db, flow_id=flow_id
                )

                # Service business logic should still work correctly
                assert result is not None
                assert result.flow_id == flow_id
                assert result.completion_rate == 1.0

    def test_service_initialization(self):
        """Test service initialization."""
        service = AnalyticsService()
        assert service is not None
        # Service should not require constructor dependencies
        assert hasattr(service, "get_flow_analytics")
        assert hasattr(service, "get_node_analytics")

    async def test_error_handling_database_failure(self, analytics_service, mock_db):
        """Test error handling when database operations fail."""
        flow_id = "error-test"

        # Mock database failure
        with patch.object(
            analytics_service,
            "_get_session_metrics",
            new=AsyncMock(side_effect=Exception("Database connection failed")),
        ):
            # Should propagate the exception (service layer doesn't catch generic exceptions)
            with pytest.raises(Exception) as exc_info:
                await analytics_service.get_flow_analytics(db=mock_db, flow_id=flow_id)

            assert "Database connection failed" in str(exc_info.value)
