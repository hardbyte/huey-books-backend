"""
Pydantic models for analytics.
"""

from typing import Any, Dict

from pydantic import BaseModel


class FlowAnalytics(BaseModel):
    flow_id: str
    total_sessions: int
    completion_rate: float
    average_duration: float
    bounce_rate: float
    engagement_metrics: Dict[str, Any]
    time_period: Dict[str, Any]


class NodeAnalytics(BaseModel):
    node_id: str
    visits: int
    interactions: int
    bounce_rate: float
    average_time_spent: float | None
    response_distribution: Dict[str, Any]
