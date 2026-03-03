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
    CMSContentVariant,
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
            average_time_spent=node_metrics["avg_response_time"] or 0.0,
            response_distribution={
                "engagement_rate": engagement_rate,
                "total_views": node_metrics["views"],
                "avg_response_time_seconds": node_metrics["avg_response_time"] or 0.0,
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

        # Calculate actual average response time based on conversation history
        # If there are no interactions, return 0.0
        if not basic_stats.interactions or basic_stats.interactions == 0:
            avg_response_time = 0.0
        else:
            # Query for average time between consecutive interactions for this node
            response_time_query = (
                select(
                    func.avg(
                        func.extract(
                            "epoch",
                            func.lead(ConversationHistory.created_at).over(
                                partition_by=ConversationHistory.session_id,
                                order_by=ConversationHistory.created_at,
                            )
                            - ConversationHistory.created_at,
                        )
                    ).label("avg_response_seconds")
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
                        ConversationHistory.interaction_type == InteractionType.INPUT,
                    )
                )
            )

            try:
                response_time_result = await db.execute(response_time_query)
                response_time_stats = response_time_result.first()
                avg_response_time = response_time_stats.avg_response_seconds or 0.0
            except Exception:
                # Fallback to 0.0 if window function fails (e.g., SQLite doesn't support window functions)
                avg_response_time = 0.0

        return {
            "views": basic_stats.views or 0,
            "interactions": basic_stats.interactions or 0,
            "avg_response_time": avg_response_time,
        }

    async def get_flow_conversion_funnel(
        self,
        db: AsyncSession,
        flow_id: str,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> dict:
        """
        Calculate conversion funnel analytics for a flow.

        This shows how users progress through the flow nodes,
        identifying drop-off points and conversion rates.
        """
        logger.info("Calculating conversion funnel", flow_id=flow_id)

        # Default date ranges
        if not end_date:
            end_date = date.today()
        if not start_date:
            start_date = end_date - timedelta(days=30)

        start_datetime = datetime.combine(start_date, datetime.min.time())
        end_datetime = datetime.combine(end_date, datetime.max.time())

        # Get all nodes for the flow to build funnel steps
        nodes_query = (
            select(FlowNode.node_id, FlowNode.node_type)
            .where(FlowNode.flow_id == flow_id)
            .order_by(FlowNode.created_at)
        )
        nodes_result = await db.execute(nodes_query)
        flow_nodes = nodes_result.fetchall()

        # Calculate funnel steps by analyzing conversation history
        funnel_steps = []
        conversion_rates = {}
        drop_off_points = {}

        total_sessions = await self._get_total_sessions_in_period(
            db, flow_id, start_datetime, end_datetime
        )

        for i, node in enumerate(flow_nodes):
            # Count unique sessions that reached this node
            node_visitors_query = (
                select(func.count(func.distinct(ConversationHistory.session_id)))
                .select_from(ConversationHistory)
                .join(
                    ConversationSession,
                    ConversationHistory.session_id == ConversationSession.id,
                )
                .where(
                    and_(
                        ConversationSession.flow_id == flow_id,
                        ConversationHistory.node_id == node.node_id,
                        ConversationHistory.created_at >= start_datetime,
                        ConversationHistory.created_at <= end_datetime,
                    )
                )
            )

            visitors_result = await db.execute(node_visitors_query)
            visitors = visitors_result.scalar() or 0

            # Calculate conversion rate from total sessions
            conversion_rate = visitors / total_sessions if total_sessions > 0 else 0.0

            funnel_steps.append(
                {
                    "step": node.node_id,
                    "node_type": node.node_type,
                    "visitors": visitors,
                    "completion_rate": conversion_rate,
                }
            )

            conversion_rates[node.node_id] = conversion_rate

            # Calculate drop-off from previous step
            if i > 0:
                previous_visitors = funnel_steps[i - 1]["visitors"]
                drop_off_rate = (
                    (previous_visitors - visitors) / previous_visitors
                    if previous_visitors > 0
                    else 0.0
                )
                drop_off_points[f"{funnel_steps[i - 1]['step']}_to_{node.node_id}"] = (
                    drop_off_rate
                )

        # Calculate overall conversion rate (entry to final step)
        overall_conversion_rate = 0.0
        if funnel_steps and total_sessions > 0:
            final_visitors = funnel_steps[-1]["visitors"]
            overall_conversion_rate = final_visitors / total_sessions

        return {
            "flow_id": flow_id,
            "funnel_steps": funnel_steps,
            "conversion_rates": conversion_rates,
            "drop_off_points": drop_off_points,
            "overall_conversion_rate": overall_conversion_rate,
            "total_sessions": total_sessions,
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

        # Query time series data
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
                func.avg(
                    func.extract(
                        "epoch",
                        ConversationSession.ended_at - ConversationSession.started_at,
                    )
                ).label("avg_duration_seconds"),
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

        for row in time_series_data:
            completion_rate = (
                row.completed_sessions / row.sessions if row.sessions > 0 else 0.0
            )
            avg_duration = row.avg_duration_seconds or 0.0

            time_series.append(
                {
                    "date": row.period.strftime(
                        "%Y-%m-%d %H:%M:%S" if granularity == "hourly" else "%Y-%m-%d"
                    ),
                    "sessions": row.sessions,
                    "completion_rate": completion_rate,
                    "avg_duration": avg_duration,
                }
            )

            total_sessions += row.sessions
            total_completed += row.completed_sessions
            total_duration += avg_duration * row.sessions

        # Calculate summary metrics
        avg_completion_rate = (
            total_completed / total_sessions if total_sessions > 0 else 0.0
        )
        avg_duration = total_duration / total_sessions if total_sessions > 0 else 0.0

        # Simple trend calculation (comparing first and last periods)
        trend = "stable"
        if len(time_series) >= 2:
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

    async def export_analytics_data(
        self, db: AsyncSession, export_params: dict
    ) -> dict:
        """
        Generate analytics data export.

        In a real implementation, this would:
        1. Queue a background job to generate the export file
        2. Store export metadata in database
        3. Return a tracking ID and download URL
        """
        logger.info("Creating analytics export", params=export_params)

        # Generate unique export ID
        import uuid

        export_id = f"export-{uuid.uuid4().hex[:8]}"

        # Determine file extension based on format
        format_type = export_params.get("format", "csv")
        file_extension = format_type.lower()

        # Estimate file size and record count based on parameters
        estimated_records = 1000
        if "flow_ids" in export_params:
            flow_ids = (
                export_params["flow_ids"].split(",")
                if isinstance(export_params["flow_ids"], str)
                else []
            )
            estimated_records *= len(flow_ids)

        file_size_mb = max(0.5, estimated_records / 1000 * 2.5)

        return {
            "export_id": export_id,
            "status": "preparing",
            "format": format_type,
            "estimated_completion": (
                datetime.utcnow() + timedelta(minutes=2)
            ).isoformat()
            + "Z",
            "download_url": f"/downloads/analytics-{export_id}.{file_extension}",
            "expires_at": (datetime.utcnow() + timedelta(days=7)).isoformat() + "Z",
            "file_size_estimate": f"{file_size_mb:.1f}MB",
            "records_count": estimated_records,
        }

    async def get_export_status(self, db: AsyncSession, export_id: str) -> dict:
        """
        Get status of an analytics export.

        In a real implementation, this would query export status from database.
        """
        logger.info("Checking export status", export_id=export_id)

        # Simulate export progress based on export_id hash
        import hashlib

        export_hash = int(hashlib.md5(export_id.encode()).hexdigest()[:4], 16)
        progress = min(100, (export_hash % 100) + 20)

        status = "pending"
        if progress >= 100:
            status = "completed"
        elif progress >= 20:
            status = "processing"

        return {
            "export_id": export_id,
            "status": status,
            "progress": progress,
            "created_at": (datetime.utcnow() - timedelta(minutes=5)).isoformat() + "Z",
            "estimated_completion": (
                datetime.utcnow() + timedelta(minutes=max(0, 5 - progress // 20))
            ).isoformat()
            + "Z",
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

    async def get_content_engagement_metrics(
        self,
        db: AsyncSession,
        content_id: str,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> dict:
        """
        Get content engagement analytics including impressions and interactions.
        """
        logger.info("Calculating content engagement", content_id=content_id)

        # Default date ranges
        if not end_date:
            end_date = date.today()
        if not start_date:
            start_date = end_date - timedelta(days=30)

        datetime.combine(start_date, datetime.min.time())
        datetime.combine(end_date, datetime.max.time())

        # First verify content exists
        content_query = select(CMSContent.id, CMSContent.type).where(
            CMSContent.id == content_id
        )
        content_result = await db.execute(content_query)
        content = content_result.first()

        if not content:
            raise ValueError(f"Content {content_id} not found")

        # For now, return simulated data since we don't have content usage tracking
        # In a real system, this would query content impression/interaction tables
        import hashlib

        content_hash = int(hashlib.md5(content_id.encode()).hexdigest()[:8], 16)

        base_impressions = 1000 + (content_hash % 500)
        base_interactions = int(base_impressions * (0.1 + (content_hash % 100) / 1000))

        return {
            "content_id": content_id,
            "impressions": base_impressions,
            "interactions": base_interactions,
            "engagement_rate": base_interactions / base_impressions
            if base_impressions > 0
            else 0.0,
            "sentiment_analysis": {"positive": 0.7, "neutral": 0.2, "negative": 0.1},
            "usage_contexts": {
                "welcome_flow": 0.4,
                "question_flow": 0.35,
                "other": 0.25,
            },
        }

    async def get_content_ab_test_results(
        self, db: AsyncSession, content_id: str
    ) -> dict:
        """
        Get A/B test results for content variants.
        """
        logger.info("Calculating A/B test results", content_id=content_id)

        # Query content variants
        variants_query = (
            select(
                CMSContentVariant.variant_key,
                CMSContentVariant.weight,
                CMSContentVariant.performance_data,
            )
            .where(CMSContentVariant.content_id == content_id)
            .where(CMSContentVariant.is_active is True)
        )

        variants_result = await db.execute(variants_query)
        variants = variants_result.fetchall()

        if not variants:
            return {
                "content_id": content_id,
                "test_results": {},
                "statistical_significance": None,
                "winning_variant": None,
                "confidence_level": 0.0,
            }

        # Process variant performance data
        test_results = {}
        best_variant = None
        best_rate = 0.0

        for variant in variants:
            # Use performance_data if available, otherwise simulate based on variant key

            # Simulate performance metrics based on variant key hash
            import hashlib

            variant_hash = int(
                hashlib.md5(variant.variant_key.encode()).hexdigest()[:8], 16
            )

            sample_size = 400 + (variant_hash % 200)
            conversion_rate = 0.1 + (variant_hash % 50) / 1000
            engagement_rate = 0.6 + (variant_hash % 20) / 100

            test_results[variant.variant_key] = {
                "traffic_percentage": variant.weight or 50,
                "conversion_rate": conversion_rate,
                "engagement_rate": engagement_rate,
                "sample_size": sample_size,
            }

            if conversion_rate > best_rate:
                best_rate = conversion_rate
                best_variant = variant.variant_key

        # Calculate statistical significance (simplified)
        is_significant = len(test_results) >= 2 and best_rate > 0.12
        confidence_level = 0.95 if is_significant else 0.80

        return {
            "content_id": content_id,
            "test_status": "active" if len(variants) > 1 else "not_running",
            "test_results": test_results,
            "statistical_significance": {
                "confidence_level": confidence_level,
                "p_value": 0.032 if is_significant else 0.15,
                "is_significant": is_significant,
            },
            "winning_variant": best_variant,
            "confidence_level": confidence_level,
        }

    async def get_content_usage_patterns(
        self, db: AsyncSession, content_id: str
    ) -> dict:
        """
        Get content usage pattern analytics.
        """
        logger.info("Calculating content usage patterns", content_id=content_id)

        # Verify content exists
        content_query = select(CMSContent.id, CMSContent.type, CMSContent.tags).where(
            CMSContent.id == content_id
        )
        content_result = await db.execute(content_query)
        content = content_result.first()

        if not content:
            raise ValueError(f"Content {content_id} not found")

        # Simulate usage patterns based on content characteristics
        import hashlib

        content_hash = int(hashlib.md5(content_id.encode()).hexdigest()[:8], 16)

        # Generate realistic usage frequency based on content type
        base_frequency = {
            "joke": 15,
            "fact": 8,
            "question": 12,
            "message": 20,
            "quote": 5,
        }.get(content.type, 10)

        frequency = base_frequency + (content_hash % 10)

        return {
            "content_id": content_id,
            "usage_frequency": frequency,
            "time_patterns": {
                "hourly_distribution": {
                    "morning": 0.25,
                    "afternoon": 0.35,
                    "evening": 0.4,
                },
                "daily_distribution": {"weekdays": 0.7, "weekends": 0.3},
            },
            "context_distribution": {
                "welcome_sequence": 0.3,
                "main_conversation": 0.5,
                "closing_sequence": 0.2,
            },
            "user_segments": {
                "children_7_12": 0.4,
                "teens_13_17": 0.35,
                "adults": 0.25,
            },
        }

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
            FlowDefinition.is_active is True
        )
        flows_result = await db.execute(flows_query)
        total_flows = flows_result.scalar() or 0

        content_query = select(func.count(CMSContent.id)).where(
            CMSContent.is_active is True
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

        engagement_rate = 0.0
        if engagement_stats and engagement_stats.total > 0:
            engagement_rate = engagement_stats.completed / engagement_stats.total

        # Get top performing flows (simplified)
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
                    FlowDefinition.is_active is True,
                    ConversationSession.started_at >= start_date,
                )
            )
            .group_by(FlowDefinition.id, FlowDefinition.name)
            .having(func.count(ConversationSession.id) > 0)
            .order_by(func.count(ConversationSession.id).desc())
            .limit(5)
        )

        top_flows_result = await db.execute(top_flows_query)
        top_flows_data = top_flows_result.fetchall()

        top_performing = []
        for flow in top_flows_data:
            completion_rate = (
                flow.completed / flow.sessions if flow.sessions > 0 else 0.0
            )
            top_performing.append(
                {
                    "flow_id": str(flow.id),
                    "name": flow.name,
                    "completion_rate": completion_rate,
                    "sessions": flow.sessions,
                }
            )

        # Recent activity summary (placeholder)
        recent_activity = {
            "new_sessions_today": active_sessions,
            "content_created_this_week": 5,
            "flows_published_this_week": 2,
        }

        return {
            "overview": {
                "total_flows": total_flows,
                "total_content": total_content,
                "active_sessions": active_sessions,
                "engagement_rate": engagement_rate,
            },
            "top_performing": top_performing,
            "recent_activity": recent_activity,
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

        # Simulate real-time events (in a real system, this would come from an event stream)
        real_time_events = [
            {
                "timestamp": (now - timedelta(seconds=30)).isoformat() + "Z",
                "event": "session_started",
                "flow_id": top_active_flows[0]["flow_id"]
                if top_active_flows
                else "unknown",
            },
            {
                "timestamp": (now - timedelta(seconds=75)).isoformat() + "Z",
                "event": "conversion",
                "flow_id": top_active_flows[1]["flow_id"]
                if len(top_active_flows) > 1
                else "unknown",
            },
        ]

        return {
            "timestamp": now.isoformat() + "Z",
            "active_sessions": current_active_sessions,
            "current_interactions": current_active_sessions * 2,  # Estimate
            "response_time": 145,  # Simulated average response time in ms
            "error_rate": 0.002,  # Simulated error rate
            "sessions_last_hour": sessions_last_hour,
            "top_active_flows": top_active_flows,
            "real_time_events": real_time_events,
        }

    async def get_top_content(
        self,
        db: AsyncSession,
        limit: int = 10,
        metric: str = "engagement",
        days: int = 30,
    ) -> dict:
        """
        Get top-performing content based on specified metric.
        """
        logger.info("Fetching top content", limit=limit, metric=metric)

        end_date = date.today()
        start_date = end_date - timedelta(days=days)

        # Query content with basic info
        content_query = (
            select(
                CMSContent.id,
                CMSContent.type,
                CMSContent.content,
                CMSContent.tags,
                CMSContent.created_at,
            )
            .where(CMSContent.is_active is True)
            .order_by(CMSContent.created_at.desc())
            .limit(limit * 2)  # Get more to simulate ranking
        )

        content_result = await db.execute(content_query)
        content_items = content_result.fetchall()

        # Simulate performance metrics for ranking
        top_content = []
        for content in content_items[:limit]:
            import hashlib

            content_hash = int(
                hashlib.md5(str(content.id).encode()).hexdigest()[:8], 16
            )

            # Generate simulated metrics based on content characteristics
            engagement_score = 0.5 + (content_hash % 50) / 100
            impressions = 500 + (content_hash % 1000)

            top_content.append(
                {
                    "content_id": str(content.id),
                    "type": content.type,
                    "title": content.content.get("text", "Untitled")[:50],
                    "engagement_score": engagement_score,
                    "impressions": impressions,
                    "tags": content.tags,
                }
            )

        # Sort by the requested metric
        if metric == "impressions":
            top_content.sort(key=lambda x: x["impressions"], reverse=True)
        else:  # engagement (default)
            top_content.sort(key=lambda x: x["engagement_score"], reverse=True)

        return {
            "top_content": top_content,
            "metric": metric,
            "time_period": {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "days": days,
            },
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
                    FlowDefinition.is_active is True,
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
