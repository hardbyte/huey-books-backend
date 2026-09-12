from unittest.mock import Mock

import pytest

from app.services import recommendation_timing


@pytest.mark.parametrize("fails", [False, True])
def test_ranking_duration_records_outcome_without_payload(monkeypatch, fails):
    histogram, logger = Mock(), Mock()
    times = iter([10.0, 10.25])
    monkeypatch.setattr(recommendation_timing, "perf_counter", lambda: next(times))
    monkeypatch.setattr(recommendation_timing, "duration", histogram)
    monkeypatch.setattr(recommendation_timing, "logger", logger)
    if fails:
        with pytest.raises(ValueError):
            with recommendation_timing.observe_recommendation():
                raise ValueError("private input")
    else:
        with recommendation_timing.observe_recommendation():
            pass
    outcome = "error" if fails else "success"
    histogram.record.assert_called_once_with(0.25, {"huey.operation.outcome": outcome})
    logger.info.assert_called_once_with(
        "Recommendation query completed",
        duration_ms=250.0,
        outcome=outcome,
    )
