from types import SimpleNamespace

import pytest

from app.services import stripe_price_cache
from app.services.stripe_price_cache import get_price_info_sync, invalidate_price_cache


def _fake_price(unit_amount=24000, currency="aud", interval="year", interval_count=1):
    return SimpleNamespace(
        unit_amount=unit_amount,
        currency=currency,
        recurring=SimpleNamespace(interval=interval, interval_count=interval_count),
    )


@pytest.fixture(autouse=True)
def _clear_cache():
    invalidate_price_cache()
    yield
    invalidate_price_cache()


def test_cache_hit_does_not_refetch(monkeypatch):
    calls = []

    def _retrieve(price_id, **kwargs):
        calls.append(price_id)
        return _fake_price()

    monkeypatch.setattr(stripe_price_cache.stripe.Price, "retrieve", _retrieve)

    first = get_price_info_sync("price_x")
    second = get_price_info_sync("price_x")

    assert first == second
    assert first.unit_amount == 24000
    assert first.interval == "year"
    assert calls == ["price_x"]


def test_invalidation_forces_refetch(monkeypatch):
    calls = []

    def _retrieve(price_id, **kwargs):
        calls.append(price_id)
        return _fake_price()

    monkeypatch.setattr(stripe_price_cache.stripe.Price, "retrieve", _retrieve)

    get_price_info_sync("price_x")
    invalidate_price_cache("price_x")
    get_price_info_sync("price_x")

    assert calls == ["price_x", "price_x"]


def test_stale_value_served_on_error_after_warm_cache(monkeypatch):
    monkeypatch.setattr(
        stripe_price_cache.stripe.Price,
        "retrieve",
        lambda price_id, **kwargs: _fake_price(unit_amount=8000),
    )
    warm = get_price_info_sync("price_x")
    assert warm.unit_amount == 8000

    def _boom(price_id, **kwargs):
        raise RuntimeError("stripe down")

    monkeypatch.setattr(stripe_price_cache.stripe.Price, "retrieve", _boom)
    # ttl_seconds=0 forces a refetch attempt, which fails and falls back to stale.
    stale = get_price_info_sync("price_x", ttl_seconds=0)
    assert stale.unit_amount == 8000


def test_error_without_cache_raises(monkeypatch):
    def _boom(price_id, **kwargs):
        raise RuntimeError("stripe down")

    monkeypatch.setattr(stripe_price_cache.stripe.Price, "retrieve", _boom)
    with pytest.raises(RuntimeError, match="stripe down"):
        get_price_info_sync("price_cold")
