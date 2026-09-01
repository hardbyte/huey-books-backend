import json
from decimal import Decimal
from unittest.mock import MagicMock

from app.services import background_tasks as bt


def test_queue_background_task_serializes_decimal_payload(monkeypatch):
    """Regression: a Stripe price.* webhook payload carries Decimal fields, which
    stdlib json.dumps rejects. queue_background_task must encode them (pydantic).
    """
    monkeypatch.setattr(bt.settings, "GCP_CLOUD_TASKS_NAME", None, raising=False)
    posted = {}

    def fake_post(url, **kwargs):
        posted["url"] = url
        posted["kwargs"] = kwargs
        return MagicMock(status_code=200)

    monkeypatch.setattr(bt.httpx, "post", fake_post)

    bt.queue_background_task(
        "process-stripe-event",
        {"amount": Decimal("500000"), "nested": {"d": Decimal("1.5")}, "n": 1},
    )

    body = posted["kwargs"]["content"]
    parsed = json.loads(body)
    assert Decimal(str(parsed["amount"])) == Decimal("500000")
    assert Decimal(str(parsed["nested"]["d"])) == Decimal("1.5")
    assert parsed["n"] == 1
    assert posted["kwargs"]["headers"]["Content-Type"] == "application/json"
