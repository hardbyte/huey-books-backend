"""
API endpoints for CMS analytics.
"""

from datetime import date
from typing import Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.async_db_dep import get_async_session
from app.api.dependencies.security import (
    get_current_active_superuser_or_backend_service_account,
)
from app.api.dependencies.service_layer import (
    get_analytics_service,
    handle_service_errors,
)
from app.models.service_account import ServiceAccount
from app.models.user import User
from app.schemas.analytics import FlowAnalytics, NodeAnalytics
from app.services.analytics import AnalyticsService

router = APIRouter()


@router.get("/flows/{flow_id}/analytics", response_model=FlowAnalytics)
@handle_service_errors
async def get_flow_analytics(
    flow_id: str,
    start_date: Optional[date] = Query(
        None, description="Start date for analytics (YYYY-MM-DD)"
    ),
    end_date: Optional[date] = Query(
        None, description="End date for analytics (YYYY-MM-DD)"
    ),
    session: AsyncSession = Depends(get_async_session),
    current_user: Union[User, ServiceAccount] = Depends(
        get_current_active_superuser_or_backend_service_account
    ),
    analytics_service: AnalyticsService = Depends(get_analytics_service),
):
    """Retrieve analytics for a specific flow."""
    return await analytics_service.get_flow_analytics(
        session, flow_id, start_date=start_date, end_date=end_date
    )


@router.get("/flows/{flow_id}/analytics/node-reach")
@handle_service_errors
async def get_flow_node_reach(
    flow_id: str,
    start_date: Optional[date] = Query(
        None, description="Start date for analytics (YYYY-MM-DD)"
    ),
    end_date: Optional[date] = Query(
        None, description="End date for analytics (YYYY-MM-DD)"
    ),
    session: AsyncSession = Depends(get_async_session),
    current_user: Union[User, ServiceAccount] = Depends(
        get_current_active_superuser_or_backend_service_account
    ),
    analytics_service: AnalyticsService = Depends(get_analytics_service),
):
    """Retrieve distinct session reach per node for a flow."""
    return await analytics_service.get_flow_node_reach(
        session, flow_id, start_date=start_date, end_date=end_date
    )


@router.get("/flows/{flow_id}/analytics/performance")
@handle_service_errors
async def get_flow_performance_over_time(
    flow_id: str,
    granularity: str = Query(
        "daily", description="Time granularity: hourly, daily, weekly"
    ),
    days: int = Query(7, description="Number of days to analyze"),
    session: AsyncSession = Depends(get_async_session),
    current_user: Union[User, ServiceAccount] = Depends(
        get_current_active_superuser_or_backend_service_account
    ),
    analytics_service: AnalyticsService = Depends(get_analytics_service),
):
    """Get flow performance metrics over time."""
    return await analytics_service.get_flow_performance_over_time(
        session, flow_id, granularity=granularity, days=days
    )


@router.get("/flows/analytics/compare")
@handle_service_errors
async def compare_flow_versions(
    flow_ids: str = Query(..., description="Comma-separated flow IDs to compare"),
    start_date: Optional[date] = Query(
        None, description="Start date for comparison (YYYY-MM-DD)"
    ),
    end_date: Optional[date] = Query(
        None, description="End date for comparison (YYYY-MM-DD)"
    ),
    session: AsyncSession = Depends(get_async_session),
    current_user: Union[User, ServiceAccount] = Depends(
        get_current_active_superuser_or_backend_service_account
    ),
    analytics_service: AnalyticsService = Depends(get_analytics_service),
):
    """Compare analytics between multiple flow versions."""
    flow_id_list = [fid.strip() for fid in flow_ids.split(",") if fid.strip()]
    if not flow_id_list:
        raise HTTPException(status_code=400, detail="No valid flow IDs provided")

    return await analytics_service.compare_flow_versions(
        session, flow_id_list, start_date=start_date, end_date=end_date
    )


@router.get("/flows/{flow_id}/nodes/{node_id}/analytics", response_model=NodeAnalytics)
@handle_service_errors
async def get_node_analytics(
    flow_id: str,
    node_id: str,
    start_date: Optional[date] = Query(
        None, description="Start date for analytics (YYYY-MM-DD)"
    ),
    end_date: Optional[date] = Query(
        None, description="End date for analytics (YYYY-MM-DD)"
    ),
    session: AsyncSession = Depends(get_async_session),
    current_user: Union[User, ServiceAccount] = Depends(
        get_current_active_superuser_or_backend_service_account
    ),
    analytics_service: AnalyticsService = Depends(get_analytics_service),
):
    """Retrieve analytics for a specific node in a flow."""
    return await analytics_service.get_node_analytics(
        session, flow_id, node_id, start_date=start_date, end_date=end_date
    )


@router.get("/analytics/dashboard")
@handle_service_errors
async def get_dashboard_metrics(
    session: AsyncSession = Depends(get_async_session),
    current_user: Union[User, ServiceAccount] = Depends(
        get_current_active_superuser_or_backend_service_account
    ),
    analytics_service: AnalyticsService = Depends(get_analytics_service),
):
    """Get high-level dashboard metrics."""
    return await analytics_service.get_dashboard_overview(
        session, user_context={"user_id": getattr(current_user, "id", None)}
    )


@router.get("/analytics/real-time")
@handle_service_errors
async def get_real_time_metrics(
    session: AsyncSession = Depends(get_async_session),
    current_user: Union[User, ServiceAccount] = Depends(
        get_current_active_superuser_or_backend_service_account
    ),
    analytics_service: AnalyticsService = Depends(get_analytics_service),
):
    """Get real-time analytics metrics."""
    return await analytics_service.get_real_time_metrics(session)


@router.get("/analytics/flows/top")
@handle_service_errors
async def get_top_flows_analytics(
    limit: int = Query(5, description="Number of top flows to return"),
    metric: str = Query(
        "completion_rate", description="Metric to rank by: completion_rate, sessions"
    ),
    session: AsyncSession = Depends(get_async_session),
    current_user: Union[User, ServiceAccount] = Depends(
        get_current_active_superuser_or_backend_service_account
    ),
    analytics_service: AnalyticsService = Depends(get_analytics_service),
):
    """Get top-performing flows analytics."""
    return await analytics_service.get_top_flows(session, limit=limit, metric=metric)
