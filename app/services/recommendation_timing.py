from contextlib import contextmanager
from time import perf_counter

from opentelemetry import metrics
from structlog import get_logger

duration = metrics.get_meter(__name__).create_histogram(
    "huey.recommendation.duration",
    unit="s",
    description="Recommendation ranking and hydration duration",
)
logger = get_logger()


@contextmanager
def observe_recommendation():
    started = perf_counter()
    outcome = "error"
    try:
        yield
        outcome = "success"
    finally:
        elapsed = perf_counter() - started
        duration.record(elapsed, {"huey.operation.outcome": outcome})
        logger.info(
            "Recommendation query completed",
            duration_ms=round(elapsed * 1000, 3),
            outcome=outcome,
        )
