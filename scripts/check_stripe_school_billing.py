"""Validate the production Stripe webhook contract used by school billing."""

import argparse

import stripe

from app.config import get_settings

REQUIRED_EVENTS = frozenset(
    {
        "checkout.session.async_payment_failed",
        "checkout.session.async_payment_succeeded",
        "checkout.session.completed",
        "checkout.session.expired",
        "customer.subscription.created",
        "customer.subscription.deleted",
        "customer.subscription.updated",
        "invoice.finalization_failed",
        "invoice.finalized",
        "invoice.marked_uncollectible",
        "invoice.paid",
        "invoice.payment_failed",
        "invoice.upcoming",
        "invoice.voided",
    }
)


def configuration_issues(
    endpoint,
    *,
    expected_api_version: str,
    expected_url: str,
    require_live_mode: bool,
) -> list[str]:
    issues: list[str] = []
    if endpoint.status != "enabled":
        issues.append(f"endpoint status is {endpoint.status!r}")
    if endpoint.url != expected_url:
        issues.append(f"endpoint URL is {endpoint.url!r}; expected {expected_url!r}")
    if require_live_mode and endpoint.livemode is not True:
        issues.append("endpoint is not in live mode")
    if endpoint.api_version != expected_api_version:
        issues.append(
            f"API version is {endpoint.api_version!r}; expected "
            f"{expected_api_version!r}"
        )
    missing_events = sorted(REQUIRED_EVENTS - set(endpoint.enabled_events))
    if missing_events:
        issues.append(f"missing events: {', '.join(missing_events)}")
    return issues


def price_configuration_issues(price, *, label: str) -> list[str]:
    """Validate a configured school price is charge-ready.

    Stripe is the source of truth for the offer's amount/currency/interval, so
    those are no longer asserted against config; we only confirm the price is
    active and recurring (a one-off or archived price would break the offer).
    """
    issues: list[str] = []
    if price.active is not True:
        issues.append(f"{label} price {price.id} is not active")
    if price.recurring is None:
        issues.append(f"{label} price {price.id} is not recurring")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint-id", required=True)
    parser.add_argument("--expected-api-version", required=True)
    parser.add_argument(
        "--expected-url",
        default="https://api.wriveted.com/v1/stripe/webhook",
    )
    parser.add_argument("--allow-test-mode", action="store_true")
    arguments = parser.parse_args()

    settings = get_settings()
    if not settings.STRIPE_SECRET_KEY:
        raise RuntimeError("STRIPE_SECRET_KEY is not configured")
    stripe.api_key = settings.STRIPE_SECRET_KEY
    endpoint = stripe.WebhookEndpoint.retrieve(arguments.endpoint_id)
    issues = configuration_issues(
        endpoint,
        expected_api_version=arguments.expected_api_version,
        expected_url=arguments.expected_url,
        require_live_mode=not arguments.allow_test_mode,
    )
    configured_prices = {"default": settings.STRIPE_SCHOOL_PRICE_IDS[0]}
    configured_prices.update(settings.STRIPE_SCHOOL_PRICE_IDS_BY_COUNTRY)
    for label, price_id in configured_prices.items():
        price = stripe.Price.retrieve(price_id)
        issues.extend(price_configuration_issues(price, label=label))
    if issues:
        for issue in issues:
            print(f"FAIL: {issue}")
        return 1
    print(f"OK: {endpoint.id} uses {endpoint.api_version} with all required events")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
