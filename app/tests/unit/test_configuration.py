from app.config import Settings


def test_can_create_settings():
    config = Settings(POSTGRESQL_PASSWORD="test", SECRET_KEY="test")
    assert hasattr(config, "SECRET_KEY")


def test_list_env_fields_parse_bare_csv_and_json(monkeypatch):
    """List[str] settings must parse from a plain env string, not just JSON.

    Regression: pydantic-settings JSON-decodes complex env values before field
    validators run, so a bare "price_x" or "a,b" value raised SettingsError and
    crashed app boot. The fields are annotated NoDecode so the CSV/JSON-aware
    validator handles them. Must be set via the ENV source (kwargs bypass it).
    """
    for name in ("POSTGRESQL_PASSWORD", "SECRET_KEY", "SHOPIFY_HMAC_SECRET"):
        monkeypatch.setenv(name, "test")
    monkeypatch.setenv("STRIPE_SCHOOL_PRICE_IDS", "price_abc")
    monkeypatch.setenv("STRIPE_SCHOOL_CONTRIBUTION_PRICE_IDS", "price_a, price_b")
    monkeypatch.setenv("STAFF_ALERT_EMAILS", '["ops@example.com"]')

    config = Settings()

    assert config.STRIPE_SCHOOL_PRICE_IDS == ["price_abc"]
    assert config.STRIPE_SCHOOL_CONTRIBUTION_PRICE_IDS == ["price_a", "price_b"]
    assert config.STAFF_ALERT_EMAILS == ["ops@example.com"]
