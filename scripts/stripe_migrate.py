#!/usr/bin/env python
"""Migrate Stripe catalog config between accounts (Wriveted -> hardbyte).

Stripe has no account-to-account clone, and price/product IDs cannot be reused
across accounts, so this reads the catalog from a SOURCE account and recreates
it in a TARGET account, printing the old->new price-ID mapping you then plug
into the app (family pricing tables) and `STRIPE_SCHOOL_PRICE_ID`.

Run it yourself with both live secret keys in env (never commit them):

    export STRIPE_SOURCE_KEY=sk_live_...   # old Wriveted account
    export STRIPE_TARGET_KEY=sk_live_...   # new hardbyte account
    export STRIPE_WEBHOOK_URL=https://wriveted-api-internal-....run.app/v1/stripe/webhook

    uv run python scripts/stripe_migrate.py export            # dump SOURCE catalog as JSON
    uv run python scripts/stripe_migrate.py migrate           # recreate in TARGET
    uv run python scripts/stripe_migrate.py migrate --live    # actually write (default is dry-run)

Covers products, prices (recurring + one-off), webhook endpoints, and billing
portal configuration. The new webhook's signing secret is printed once on
creation — copy it into the STRIPE_WEBHOOK_SECRET secret.
"""

import argparse
import json
import os
import sys

import stripe


def _key(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"{name} is not set")
    return value


def _catalog(api_key: str) -> dict:
    products = list(
        stripe.Product.list(active=True, limit=100, api_key=api_key).auto_paging_iter()
    )
    prices = list(
        stripe.Price.list(active=True, limit=100, api_key=api_key).auto_paging_iter()
    )
    webhooks = list(
        stripe.WebhookEndpoint.list(limit=100, api_key=api_key).auto_paging_iter()
    )
    portals = list(
        stripe.billing_portal.Configuration.list(
            limit=100, api_key=api_key
        ).auto_paging_iter()
    )
    return {
        "products": products,
        "prices": prices,
        "webhooks": webhooks,
        "portals": portals,
    }


def cmd_export() -> None:
    cat = _catalog(_key("STRIPE_SOURCE_KEY"))
    summary = {
        "products": [
            {"id": p.id, "name": p.name, "active": p.active} for p in cat["products"]
        ],
        "prices": [
            {
                "id": pr.id,
                "product": pr.product,
                "currency": pr.currency,
                "unit_amount": pr.unit_amount,
                "nickname": pr.nickname,
                "recurring": dict(pr.recurring) if pr.recurring else None,
            }
            for pr in cat["prices"]
        ],
        "webhooks": [
            {"url": w.url, "enabled_events": w.enabled_events} for w in cat["webhooks"]
        ],
        "portals": [{"id": c.id, "is_default": c.is_default} for c in cat["portals"]],
    }
    print(json.dumps(summary, indent=2, default=str))


def cmd_migrate(live: bool) -> None:
    source = _key("STRIPE_SOURCE_KEY")
    target = _key("STRIPE_TARGET_KEY")
    cat = _catalog(source)
    tag = "" if live else "[DRY-RUN] "

    # Products (id -> new id)
    product_map: dict[str, str] = {}
    for p in cat["products"]:
        print(f"{tag}create product {p.name!r} (from {p.id})")
        if live:
            new = stripe.Product.create(
                name=p.name, description=p.description or None, api_key=target
            )
            product_map[p.id] = new.id

    # Prices (old id -> new id) — the mapping you need downstream
    price_map: dict[str, str] = {}
    for pr in cat["prices"]:
        amount = f"{(pr.unit_amount or 0) / 100:.2f} {pr.currency.upper()}"
        interval = pr.recurring["interval"] if pr.recurring else "one-off"
        print(
            f"{tag}create price {amount} / {interval} for product {pr.product} (from {pr.id})"
        )
        if live:
            params = {
                "currency": pr.currency,
                "unit_amount": pr.unit_amount,
                "product": product_map.get(pr.product, pr.product),
                "nickname": pr.nickname or None,
                "api_key": target,
            }
            if pr.recurring:
                params["recurring"] = {"interval": pr.recurring["interval"]}
            new = stripe.Price.create(**params)
            price_map[pr.id] = new.id

    # Webhook endpoint(s)
    webhook_url = os.environ.get("STRIPE_WEBHOOK_URL")
    for w in cat["webhooks"]:
        url = webhook_url or w.url
        print(f"{tag}create webhook -> {url} ({len(w.enabled_events)} events)")
        if live:
            new = stripe.WebhookEndpoint.create(
                url=url, enabled_events=w.enabled_events, api_key=target
            )
            print(f"  >>> STRIPE_WEBHOOK_SECRET (set this secret): {new.secret}")

    # Billing / customer portal configuration
    for c in cat["portals"]:
        print(
            f"{tag}create billing portal configuration (from {c.id}, default={c.is_default})"
        )
        if live:
            stripe.billing_portal.Configuration.create(
                business_profile=dict(c.business_profile),
                features=json.loads(json.dumps(c.features, default=str)),
                api_key=target,
            )

    if live and price_map:
        print(
            "\n=== OLD -> NEW price id mapping (update app + STRIPE_SCHOOL_PRICE_ID) ==="
        )
        print(json.dumps(price_map, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["export", "migrate"])
    parser.add_argument(
        "--live",
        action="store_true",
        help="actually write to TARGET (default: dry-run)",
    )
    args = parser.parse_args()
    if args.command == "export":
        cmd_export()
    else:
        cmd_migrate(live=args.live)


if __name__ == "__main__":
    main()
