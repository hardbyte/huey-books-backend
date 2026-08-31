"""Cached resolver for a Stripe Price's charge terms.

The Stripe Price object is the single source of truth for a school offer's
amount/currency/interval; config only points at the price id. Reads are cached
with a TTL so billing-status does not call Stripe on every request, and the
Stripe webhook invalidates an entry when a price changes.
"""

import asyncio
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta

import stripe
from structlog import get_logger

from app.config import get_settings

logger = get_logger()

DEFAULT_TTL_SECONDS = 3600


@dataclass(frozen=True)
class StripePriceInfo:
    unit_amount: int
    currency: str
    interval: str
    interval_count: int


@dataclass
class _CacheEntry:
    info: StripePriceInfo
    fetched_at: datetime


_cache: dict[str, _CacheEntry] = {}
_cache_lock = threading.Lock()


def invalidate_price_cache(price_id: str | None = None) -> None:
    """Drop one cached price (or all) so the next read refetches from Stripe."""
    with _cache_lock:
        if price_id is None:
            _cache.clear()
        else:
            _cache.pop(price_id, None)


def _retrieve_price_info(price_id: str) -> StripePriceInfo:
    settings = get_settings()
    stripe.api_key = settings.STRIPE_SECRET_KEY
    price = stripe.Price.retrieve(price_id)
    recurring = price.recurring
    if recurring is None:
        raise ValueError(f"Stripe price {price_id} is not recurring")
    return StripePriceInfo(
        unit_amount=price.unit_amount,
        currency=price.currency,
        interval=recurring.interval,
        interval_count=recurring.interval_count,
    )


def get_price_info_sync(
    price_id: str, *, ttl_seconds: int = DEFAULT_TTL_SECONDS
) -> StripePriceInfo:
    """Return a price's charge terms, fetching from Stripe on a cold/expired cache.

    Blocking: only call this from a sync context, or via ``get_price_info`` (which
    offloads it to a thread) from async code.

    On a Stripe error, a previously-cached value (even if stale) is returned with
    a warning; with no cached value at all the error propagates to the caller.
    """
    now = datetime.utcnow()
    with _cache_lock:
        entry = _cache.get(price_id)
    if entry is not None and now - entry.fetched_at < timedelta(seconds=ttl_seconds):
        return entry.info

    try:
        info = _retrieve_price_info(price_id)
    except Exception as error:
        if entry is not None:
            logger.warning(
                "Stripe price refresh failed; serving stale cached value",
                price_id=price_id,
                error=str(error),
            )
            return entry.info
        raise

    with _cache_lock:
        _cache[price_id] = _CacheEntry(info=info, fetched_at=now)
    return info


async def get_price_info(
    price_id: str, *, ttl_seconds: int = DEFAULT_TTL_SECONDS
) -> StripePriceInfo:
    """Async wrapper around ``get_price_info_sync``.

    The Stripe SDK is blocking, so a cache miss is offloaded to a worker thread to
    avoid stalling the event loop (this service runs multiple requests per
    instance).
    """
    return await asyncio.to_thread(
        get_price_info_sync, price_id, ttl_seconds=ttl_seconds
    )
