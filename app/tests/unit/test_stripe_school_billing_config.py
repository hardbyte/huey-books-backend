from types import SimpleNamespace

from scripts.check_stripe_school_billing import (
    REQUIRED_EVENTS,
    configuration_issues,
    price_configuration_issues,
)


def test_configuration_issues_accepts_exact_contract():
    endpoint = SimpleNamespace(
        status="enabled",
        url="https://api.wriveted.com/v1/stripe/webhook",
        livemode=True,
        api_version="2026-07-29.dahlia",
        enabled_events=list(REQUIRED_EVENTS),
    )

    assert (
        configuration_issues(
            endpoint,
            expected_api_version="2026-07-29.dahlia",
            expected_url="https://api.wriveted.com/v1/stripe/webhook",
            require_live_mode=True,
        )
        == []
    )


def test_configuration_issues_reports_version_status_and_events():
    endpoint = SimpleNamespace(
        status="disabled",
        url="https://example.com/wrong",
        livemode=False,
        api_version=None,
        enabled_events=[],
    )

    issues = configuration_issues(
        endpoint,
        expected_api_version="2026-07-29.dahlia",
        expected_url="https://api.wriveted.com/v1/stripe/webhook",
        require_live_mode=True,
    )

    assert "endpoint status is 'disabled'" in issues
    assert any(issue.startswith("endpoint URL is") for issue in issues)
    assert "endpoint is not in live mode" in issues
    assert "API version is None; expected '2026-07-29.dahlia'" in issues
    assert any(issue.startswith("missing events:") for issue in issues)


def test_price_configuration_accepts_disclosed_recurring_price():
    price = SimpleNamespace(
        id="price_school",
        active=True,
        unit_amount=8000,
        currency="aud",
        recurring=SimpleNamespace(interval="year", interval_count=1),
    )

    assert (
        price_configuration_issues(
            price,
            label="IND",
            expected_unit_amount=8000,
            expected_currency="aud",
            expected_interval="year",
            expected_interval_count=1,
        )
        == []
    )


def test_price_configuration_reports_display_mismatch():
    price = SimpleNamespace(
        id="price_school",
        active=True,
        unit_amount=24000,
        currency="nzd",
        recurring=SimpleNamespace(interval="month", interval_count=6),
    )

    issues = price_configuration_issues(
        price,
        label="IND",
        expected_unit_amount=8000,
        expected_currency="aud",
        expected_interval="year",
        expected_interval_count=1,
    )

    assert len(issues) == 3
