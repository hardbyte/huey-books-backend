"""
Analytics Service - Domain service for conversation flow analytics.

This service extracts analytics logic from CRUD layer to demonstrate proper
service layer architecture improvements.
"""

from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import and_, case, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from app.models.cms import (
    CMSContent,
    ConversationHistory,
    ConversationSession,
    FlowDefinition,
    FlowNode,
    InteractionType,
    SessionStatus,
)
from app.schemas.analytics import FlowAnalytics, NodeAnalytics

logger = get_logger()


class AnalyticsService:
    """
    Service for conversation flow analytics.

    This service demonstrates proper service layer architecture by:
    - Separating business logic from CRUD operations
    - Using direct repository access without unnecessary transactions for reads
    - Providing domain-focused methods rather than generic CRUD operations
    """

    async def get_flow_analytics(
        self,
        db: AsyncSession,
        flow_id: str,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> FlowAnalytics:
        """
        Calculate comprehensive analytics for a conversation flow.

        This method demonstrates service layer business logic:
        - Date range defaulting and validation
        - Complex multi-table analytics calculations
        - Domain object construction
        """
        logger.info(
            "Calculating flow analytics",
            flow_id=flow_id,
            start_date=start_date,
            end_date=end_date,
        )

        # Business logic: Default date ranges
        if not end_date:
            end_date = date.today()
        if not start_date:
            start_date = end_date - timedelta(days=30)

        # Convert dates to datetime for comparison
        start_datetime = datetime.combine(start_date, datetime.min.time())
        end_datetime = datetime.combine(end_date, datetime.max.time())

        # Direct repository access - no transaction needed for read operations
        session_metrics = await self._get_session_metrics(
            db, flow_id, start_datetime, end_datetime
        )

        interaction_metrics = await self._get_interaction_metrics(
            db, flow_id, start_datetime, end_datetime
        )

        # Business logic: Calculate derived metrics
        completion_rate = 0.0
        if session_metrics["total_sessions"] > 0:
            completion_rate = (
                session_metrics["completed_sessions"]
                / session_metrics["total_sessions"]
            )

        avg_duration_minutes = 0.0
        if session_metrics["avg_duration_seconds"]:
            avg_duration_minutes = float(session_metrics["avg_duration_seconds"]) / 60.0

        # Domain object construction matching existing schema
        return FlowAnalytics(
            flow_id=flow_id,
            total_sessions=session_metrics["total_sessions"],
            completion_rate=completion_rate,
            average_duration=avg_duration_minutes,
            bounce_rate=1.0 - completion_rate,  # Simple bounce rate calculation
            engagement_metrics={
                "total_interactions": interaction_metrics["total_interactions"],
                "unique_users": session_metrics["unique_users"],
                "completed_sessions": session_metrics["completed_sessions"],
            },
            time_period={
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "days": (end_date - start_date).days,
            },
        )

    async def get_node_analytics(
        self,
        db: AsyncSession,
        flow_id: str,
        node_id: str,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> NodeAnalytics:
        """
        Calculate analytics for a specific node within a flow.

        This method demonstrates:
        - Node-specific business logic
        - Engagement rate calculations
        - Response time analysis
        """
        logger.info("Calculating node analytics", flow_id=flow_id, node_id=node_id)

        # Business logic: Default date ranges
        if not end_date:
            end_date = date.today()
        if not start_date:
            start_date = end_date - timedelta(days=30)

        start_datetime = datetime.combine(start_date, datetime.min.time())
        end_datetime = datetime.combine(end_date, datetime.max.time())

        try:
            # Direct repository access for node-specific metrics
            node_metrics = await self._get_node_metrics(
                db, flow_id, node_id, start_datetime, end_datetime
            )
            logger.info("Retrieved node metrics", node_metrics=node_metrics)
        except Exception as e:
            logger.error(
                "Error getting node metrics",
                error=str(e),
                flow_id=flow_id,
                node_id=node_id,
            )
            raise

        # Business logic: Calculate engagement rate
        engagement_rate = 0.0
        bounce_rate = 0.0

        if node_metrics["views"] > 0:
            engagement_rate = node_metrics["interactions"] / node_metrics["views"]
            # Calculate bounce rate for node (simplified as 1 - engagement_rate)
            bounce_rate = max(0.0, 1.0 - engagement_rate)
        # If no views, both engagement and bounce rate remain 0.0

        return NodeAnalytics(
            node_id=node_id,
            visits=node_metrics["views"],
            interactions=node_metrics["interactions"],
            bounce_rate=bounce_rate,
            average_time_spent=None,
            response_distribution={
                "engagement_rate": engagement_rate,
                "total_views": node_metrics["views"],
            },
        )

    async def _get_session_metrics(
        self,
        db: AsyncSession,
        flow_id: str,
        start_datetime: datetime,
        end_datetime: datetime,
    ) -> dict:
        """Private method for session-level metrics calculation."""
        query = (
            select(
                func.count(distinct(ConversationSession.id)).label("total_sessions"),
                func.count(
                    case(
                        (ConversationSession.status == SessionStatus.COMPLETED, 1),
                        else_=None,
                    )
                ).label("completed_sessions"),
                func.count(distinct(ConversationSession.user_id)).label("unique_users"),
                func.avg(
                    func.extract(
                        "epoch",
                        ConversationSession.ended_at - ConversationSession.started_at,
                    )
                ).label("avg_duration_seconds"),
            )
            .select_from(ConversationSession)
            .where(
                and_(
                    ConversationSession.flow_id == flow_id,
                    ConversationSession.started_at >= start_datetime,
                    ConversationSession.started_at <= end_datetime,
                )
            )
        )

        result = await db.execute(query)
        stats = result.first()

        return {
            "total_sessions": stats.total_sessions or 0,
            "completed_sessions": stats.completed_sessions or 0,
            "unique_users": stats.unique_users or 0,
            "avg_duration_seconds": stats.avg_duration_seconds,
        }

    async def _get_interaction_metrics(
        self,
        db: AsyncSession,
        flow_id: str,
        start_datetime: datetime,
        end_datetime: datetime,
    ) -> dict:
        """Private method for interaction-level metrics calculation."""
        query = (
            select(func.count(ConversationHistory.id).label("total_interactions"))
            .select_from(ConversationHistory)
            .join(
                ConversationSession,
                ConversationHistory.session_id == ConversationSession.id,
            )
            .where(
                and_(
                    ConversationSession.flow_id == flow_id,
                    ConversationHistory.created_at >= start_datetime,
                    ConversationHistory.created_at <= end_datetime,
                    ConversationHistory.interaction_type == InteractionType.INPUT,
                )
            )
        )

        result = await db.execute(query)
        stats = result.first()

        return {"total_interactions": stats.total_interactions or 0}

    async def _get_node_metrics(
        self,
        db: AsyncSession,
        flow_id: str,
        node_id: str,
        start_datetime: datetime,
        end_datetime: datetime,
    ) -> dict:
        """Private method for node-specific metrics calculation."""
        # Query for basic node views and interactions (without window function)
        basic_query = (
            select(
                func.count(ConversationHistory.id).label("views"),
                func.count(
                    case(
                        (
                            ConversationHistory.interaction_type
                            == InteractionType.INPUT,
                            1,
                        ),
                        else_=None,
                    )
                ).label("interactions"),
            )
            .select_from(ConversationHistory)
            .join(
                ConversationSession,
                ConversationHistory.session_id == ConversationSession.id,
            )
            .where(
                and_(
                    ConversationSession.flow_id == flow_id,
                    ConversationHistory.node_id == node_id,
                    ConversationHistory.created_at >= start_datetime,
                    ConversationHistory.created_at <= end_datetime,
                )
            )
        )

        basic_result = await db.execute(basic_query)
        basic_stats = basic_result.first()

        return {
            "views": basic_stats.views or 0,
            "interactions": basic_stats.interactions or 0,
        }

    async def get_flow_node_reach(
        self,
        db: AsyncSession,
        flow_id: str,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> dict:
        """Count distinct cohort sessions reaching each node by the cutoff."""
        end_date = end_date or date.today()
        start_date = start_date or end_date - timedelta(days=30)
        if start_date > end_date:
            raise ValueError("Start date must not be after end date")
        start_datetime = datetime.combine(start_date, datetime.min.time())
        end_datetime = datetime.combine(end_date, datetime.max.time())
        nodes = (
            await db.execute(
                select(FlowNode.node_id, FlowNode.node_type)
                .where(FlowNode.flow_id == flow_id)
                .order_by(FlowNode.node_id)
            )
        ).all()
        total_sessions = await self._get_total_sessions_in_period(
            db, flow_id, start_datetime, end_datetime
        )
        counts = (
            await db.execute(
                select(
                    ConversationHistory.node_id,
                    func.count(distinct(ConversationHistory.session_id)).label(
                        "sessions"
                    ),
                )
                .join(
                    ConversationSession,
                    ConversationHistory.session_id == ConversationSession.id,
                )
                .where(
                    ConversationSession.flow_id == flow_id,
                    ConversationSession.started_at >= start_datetime,
                    ConversationSession.started_at <= end_datetime,
                    ConversationHistory.created_at <= end_datetime,
                )
                .group_by(ConversationHistory.node_id)
            )
        ).all()
        by_node = {row.node_id: row.sessions for row in counts}
        return {
            "flow_id": flow_id,
            "total_sessions": total_sessions,
            "time_period": {"start_date": start_date, "end_date": end_date},
            "nodes": [
                {
                    "node_id": node.node_id,
                    "node_type": node.node_type,
                    "sessions": by_node.get(node.node_id, 0),
                    "reached_fraction": by_node.get(node.node_id, 0) / total_sessions
                    if total_sessions
                    else None,
                }
                for node in nodes
            ],
        }

    async def get_flow_performance_over_time(
        self, db: AsyncSession, flow_id: str, granularity: str = "daily", days: int = 7
    ) -> dict:
        """
        Get flow performance metrics over time with specified granularity.
        """
        logger.info(
            "Calculating flow performance over time",
            flow_id=flow_id,
            granularity=granularity,
        )

        end_date = date.today()
        start_date = end_date - timedelta(days=days)
        start_datetime = datetime.combine(start_date, datetime.min.time())
        end_datetime = datetime.combine(end_date, datetime.max.time())

        # Build date truncation function based on granularity
        if granularity == "hourly":
            date_trunc = func.date_trunc("hour", ConversationSession.started_at)
        elif granularity == "weekly":
            date_trunc = func.date_trunc("week", ConversationSession.started_at)
        else:  # daily (default)
            date_trunc = func.date_trunc("day", ConversationSession.started_at)

        duration_seconds = case(
            (
                ConversationSession.ended_at >= ConversationSession.started_at,
                func.extract(
                    "epoch",
                    ConversationSession.ended_at - ConversationSession.started_at,
                ),
            ),
            else_=None,
        )
        time_series_query = (
            select(
                date_trunc.label("period"),
                func.count(ConversationSession.id).label("sessions"),
                func.count(
                    case(
                        (ConversationSession.status == SessionStatus.COMPLETED, 1),
                        else_=None,
                    )
                ).label("completed_sessions"),
                func.avg(duration_seconds).label("avg_duration_seconds"),
                func.count(duration_seconds).label("duration_observations"),
            )
            .where(
                and_(
                    ConversationSession.flow_id == flow_id,
                    ConversationSession.started_at >= start_datetime,
                    ConversationSession.started_at <= end_datetime,
                )
            )
            .group_by("period")
            .order_by("period")
        )

        result = await db.execute(time_series_query)
        time_series_data = result.fetchall()

        # Process results into time series format
        time_series = []
        total_sessions = 0
        total_completed = 0
        total_duration = 0
        total_duration_observations = 0

        for row in time_series_data:
            completion_rate = (
                row.completed_sessions / row.sessions if row.sessions > 0 else 0.0
            )
            avg_duration = row.avg_duration_seconds

            time_series.append(
                {
                    "date": row.period.strftime(
                        "%Y-%m-%d %H:%M:%S" if granularity == "hourly" else "%Y-%m-%d"
                    ),
                    "sessions": row.sessions,
                    "completion_rate": completion_rate,
                    "avg_duration": avg_duration,
                    "duration_observations": row.duration_observations,
                }
            )

            total_sessions += row.sessions
            total_completed += row.completed_sessions
            total_duration += (avg_duration or 0) * row.duration_observations
            total_duration_observations += row.duration_observations

        # Calculate summary metrics
        avg_completion_rate = (
            total_completed / total_sessions if total_sessions > 0 else None
        )
        avg_duration = (
            total_duration / total_duration_observations
            if total_duration_observations
            else None
        )

        # Simple trend calculation (comparing first and last periods)
        trend = None
        if len(time_series) >= 2:
            trend = "stable"
            first_rate = time_series[0]["completion_rate"]
            last_rate = time_series[-1]["completion_rate"]
            if last_rate > first_rate * 1.05:  # 5% increase
                trend = "improving"
            elif last_rate < first_rate * 0.95:  # 5% decrease
                trend = "declining"

        return {
            "flow_id": flow_id,
            "granularity": granularity,
            "time_series": time_series,
            "summary": {
                "total_sessions": total_sessions,
                "avg_completion_rate": avg_completion_rate,
                "avg_duration": avg_duration,
                "duration_observations": total_duration_observations,
                "trend": trend,
            },
        }

    async def compare_flow_versions(
        self,
        db: AsyncSession,
        flow_ids: list[str],
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> dict:
        """
        Compare analytics between multiple flow versions.
        """
        logger.info("Comparing flow versions", flow_ids=flow_ids)

        if not end_date:
            end_date = date.today()
        if not start_date:
            start_date = end_date - timedelta(days=30)

        start_datetime = datetime.combine(start_date, datetime.min.time())
        end_datetime = datetime.combine(end_date, datetime.max.time())

        comparison = []

        for flow_id in flow_ids:
            # Get basic metrics for each flow
            session_metrics = await self._get_session_metrics(
                db, flow_id, start_datetime, end_datetime
            )

            completion_rate = 0.0
            if session_metrics["total_sessions"] > 0:
                completion_rate = (
                    session_metrics["completed_sessions"]
                    / session_metrics["total_sessions"]
                )

            avg_duration = session_metrics["avg_duration_seconds"] or 0.0

            comparison.append(
                {
                    "flow_id": flow_id,
                    "sessions": session_metrics["total_sessions"],
                    "completion_rate": completion_rate,
                    "avg_duration": avg_duration,
                    "unique_users": session_metrics["unique_users"],
                }
            )

        # Identify best performing flow
        best_flow = None
        best_score = -1

        for flow_data in comparison:
            # Simple scoring: completion_rate * sessions (weighted by volume)
            score = flow_data["completion_rate"] * flow_data["sessions"]
            if score > best_score:
                best_score = score
                best_flow = flow_data["flow_id"]

        # Calculate performance delta
        performance_delta = None
        if len(comparison) >= 2 and best_flow:
            best_data = next(f for f in comparison if f["flow_id"] == best_flow)
            others = [f for f in comparison if f["flow_id"] != best_flow]
            avg_other_rate = sum(f["completion_rate"] for f in others) / len(others)

            if avg_other_rate > 0:
                improvement = (
                    (best_data["completion_rate"] - avg_other_rate) / avg_other_rate
                ) * 100
                performance_delta = {
                    "best_performing": best_flow,
                    "improvement_percentage": improvement,
                }

        return {
            "comparison": comparison,
            "performance_delta": performance_delta,
            "winner": best_flow,
        }

    async def _get_total_sessions_in_period(
        self,
        db: AsyncSession,
        flow_id: str,
        start_datetime: datetime,
        end_datetime: datetime,
    ) -> int:
        """Helper to get total sessions for a flow in a time period."""
        query = select(func.count(ConversationSession.id)).where(
            and_(
                ConversationSession.flow_id == flow_id,
                ConversationSession.started_at >= start_datetime,
                ConversationSession.started_at <= end_datetime,
            )
        )
        result = await db.execute(query)
        return result.scalar() or 0

    async def get_dashboard_overview(
        self, db: AsyncSession, user_context: Optional[dict] = None
    ) -> dict:
        """
        Get high-level dashboard metrics and overview data.
        """
        logger.info("Generating dashboard overview")

        # Get current date ranges for metrics
        end_date = datetime.utcnow()
        start_date = end_date - timedelta(days=30)

        # Get flow and content counts
        flows_query = select(func.count(FlowDefinition.id)).where(
            FlowDefinition.is_active.is_(True)
        )
        flows_result = await db.execute(flows_query)
        total_flows = flows_result.scalar() or 0

        content_query = select(func.count(CMSContent.id)).where(
            CMSContent.is_active.is_(True)
        )
        content_result = await db.execute(content_query)
        total_content = content_result.scalar() or 0

        # Get active sessions count
        active_sessions_query = select(func.count(ConversationSession.id)).where(
            ConversationSession.status == SessionStatus.ACTIVE
        )
        active_sessions_result = await db.execute(active_sessions_query)
        active_sessions = active_sessions_result.scalar() or 0

        # Calculate engagement rate from recent sessions
        recent_sessions_query = select(
            func.count(ConversationSession.id).label("total"),
            func.count(
                case(
                    (ConversationSession.status == SessionStatus.COMPLETED, 1),
                    else_=None,
                )
            ).label("completed"),
        ).where(
            and_(
                ConversationSession.started_at >= start_date,
                ConversationSession.started_at <= end_date,
            )
        )

        engagement_result = await db.execute(recent_sessions_query)
        engagement_stats = engagement_result.first()

        completion_rate = None
        if engagement_stats and engagement_stats.total > 0:
            completion_rate = engagement_stats.completed / engagement_stats.total

        top_flows_query = (
            select(
                FlowDefinition.id,
                FlowDefinition.name,
                func.count(ConversationSession.id).label("sessions"),
                func.count(
                    case(
                        (ConversationSession.status == SessionStatus.COMPLETED, 1),
                        else_=None,
                    )
                ).label("completed"),
            )
            .join(ConversationSession, FlowDefinition.id == ConversationSession.flow_id)
            .where(
                and_(
                    FlowDefinition.is_active.is_(True),
                    ConversationSession.started_at >= start_date,
                    ConversationSession.started_at <= end_date,
                )
            )
            .group_by(FlowDefinition.id, FlowDefinition.name)
            .having(func.count(ConversationSession.id) > 0)
            .order_by(func.count(ConversationSession.id).desc())
            .limit(5)
        )

        top_flows_result = await db.execute(top_flows_query)
        top_flows_data = top_flows_result.fetchall()

        top_flows_by_sessions = []
        for flow in top_flows_data:
            top_flows_by_sessions.append(
                {
                    "flow_id": str(flow.id),
                    "name": flow.name,
                    "completion_rate": flow.completed / flow.sessions,
                    "sessions": flow.sessions,
                }
            )

        return {
            "overview": {
                "total_flows": total_flows,
                "total_content": total_content,
                "active_sessions": active_sessions,
                "completion_rate": completion_rate,
            },
            "top_flows_by_sessions": top_flows_by_sessions,
        }

    async def get_real_time_metrics(self, db: AsyncSession) -> dict:
        """
        Get real-time analytics metrics for system monitoring.
        """
        logger.info("Fetching real-time metrics")

        now = datetime.utcnow()
        hour_ago = now - timedelta(hours=1)

        # Current active sessions
        active_sessions_query = select(func.count(ConversationSession.id)).where(
            ConversationSession.status == SessionStatus.ACTIVE
        )
        active_sessions_result = await db.execute(active_sessions_query)
        current_active_sessions = active_sessions_result.scalar() or 0

        # Sessions in last hour
        recent_sessions_query = select(func.count(ConversationSession.id)).where(
            ConversationSession.started_at >= hour_ago
        )
        recent_sessions_result = await db.execute(recent_sessions_query)
        sessions_last_hour = recent_sessions_result.scalar() or 0

        # Top active flows
        top_active_query = (
            select(
                FlowDefinition.id,
                FlowDefinition.name,
                func.count(ConversationSession.id).label("active_sessions"),
            )
            .join(ConversationSession, FlowDefinition.id == ConversationSession.flow_id)
            .where(ConversationSession.status == SessionStatus.ACTIVE)
            .group_by(FlowDefinition.id, FlowDefinition.name)
            .order_by(func.count(ConversationSession.id).desc())
            .limit(5)
        )

        top_active_result = await db.execute(top_active_query)
        top_active_flows = [
            {
                "flow_id": str(row.id),
                "name": row.name,
                "active_sessions": row.active_sessions,
            }
            for row in top_active_result.fetchall()
        ]

        return {
            "timestamp": now.isoformat() + "Z",
            "active_sessions": current_active_sessions,
            "sessions_last_hour": sessions_last_hour,
            "top_active_flows": top_active_flows,
        }

    async def get_top_flows(
        self,
        db: AsyncSession,
        limit: int = 5,
        metric: str = "completion_rate",
        days: int = 30,
    ) -> dict:
        """
        Get top-performing flows based on specified metric.
        """
        logger.info("Fetching top flows", limit=limit, metric=metric)

        end_date = datetime.utcnow()
        start_date = end_date - timedelta(days=days)

        # Query flows with session metrics
        flows_query = (
            select(
                FlowDefinition.id,
                FlowDefinition.name,
                FlowDefinition.version,
                func.count(ConversationSession.id).label("total_sessions"),
                func.count(
                    case(
                        (ConversationSession.status == SessionStatus.COMPLETED, 1),
                        else_=None,
                    )
                ).label("completed_sessions"),
            )
            .join(ConversationSession, FlowDefinition.id == ConversationSession.flow_id)
            .where(
                and_(
                    FlowDefinition.is_active.is_(True),
                    ConversationSession.started_at >= start_date,
                    ConversationSession.started_at <= end_date,
                )
            )
            .group_by(FlowDefinition.id, FlowDefinition.name, FlowDefinition.version)
            .having(func.count(ConversationSession.id) > 0)
        )

        flows_result = await db.execute(flows_query)
        flows_data = flows_result.fetchall()

        # Process and rank flows
        top_flows = []
        for flow in flows_data:
            completion_rate = (
                flow.completed_sessions / flow.total_sessions
                if flow.total_sessions > 0
                else 0.0
            )

            top_flows.append(
                {
                    "flow_id": str(flow.id),
                    "name": flow.name,
                    "version": flow.version,
                    "completion_rate": completion_rate,
                    "total_sessions": flow.total_sessions,
                    "completed_sessions": flow.completed_sessions,
                }
            )

        # Sort by the requested metric
        if metric == "sessions":
            top_flows.sort(key=lambda x: x["total_sessions"], reverse=True)
        else:  # completion_rate (default)
            top_flows.sort(key=lambda x: x["completion_rate"], reverse=True)

        return {
            "top_flows": top_flows[:limit],
            "metric": metric,
            "time_period": {
                "start_date": start_date.date().isoformat(),
                "end_date": end_date.date().isoformat(),
                "days": days,
            },
        }
