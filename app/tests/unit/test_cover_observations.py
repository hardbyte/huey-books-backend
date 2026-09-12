from unittest.mock import patch

import pytest
from pydantic import ValidationError

from app.schemas.browser_observations import CoverObservation, TimingObservation
from app.services.browser_observations import (
    BrowserObservationService,
    InvalidObservationReceipt,
    create_cover_receipt,
)


@pytest.mark.parametrize("kind", ["exact", "alternative", "placeholder"])
def test_every_cover_kind_uses_the_same_deduplication_and_sampling_metadata(kind):
    service = BrowserObservationService()
    receipt = create_cover_receipt(kind, "test-key")
    observation = CoverObservation(
        event="cover_presented", schema_version=1, cover_kind=kind
    )
    with patch("app.services.browser_observations.logger") as logger:
        service.record(observation, receipt, "test-key")
        service.record(observation, receipt, "test-key")
    logger.info.assert_called_once()
    fields = logger.info.call_args.kwargs
    assert fields["cover_kind"] == kind
    assert fields["sampling_probability"] == 0.1
    assert set(fields) == {
        "observation_id",
        "source_surface",
        "cover_kind",
        "traffic_class",
        "schema_version",
        "received_at",
        "sampling_probability",
    }


def test_disclosure_is_separate_and_broken_image_is_a_placeholder():
    service = BrowserObservationService()
    receipt = create_cover_receipt("alternative", "key")
    with patch("app.services.browser_observations.logger") as logger:
        for event in ["cover_presented", "cover_disclosure_viewed"]:
            service.record(
                CoverObservation(
                    event=event, schema_version=1, cover_kind="alternative"
                ),
                receipt,
                "key",
            )
        service.record(
            CoverObservation(
                event="cover_presented", schema_version=1, cover_kind="placeholder"
            ),
            create_cover_receipt("exact", "key"),
            "key",
        )
    assert logger.info.call_count == 3


@pytest.mark.parametrize(
    "issued,rendered,event",
    [
        ("exact", "alternative", "cover_presented"),
        ("placeholder", "exact", "cover_presented"),
        ("exact", "exact", "cover_disclosure_viewed"),
        ("alternative", "placeholder", "cover_disclosure_viewed"),
    ],
)
def test_receipt_does_not_authorize_other_cover_claims(issued, rendered, event):
    with pytest.raises(InvalidObservationReceipt):
        BrowserObservationService().record(
            CoverObservation(event=event, schema_version=1, cover_kind=rendered),
            create_cover_receipt(issued, "key"),
            "key",
        )


def test_cover_receipt_does_not_authorize_timing():
    observation = TimingObservation(
        event="response_timing",
        schema_version=1,
        operation="start",
        request_duration_ms=1,
        response_to_commit_ms=1,
        response_to_next_frame_ms=1,
    )
    with pytest.raises(InvalidObservationReceipt):
        BrowserObservationService().record(
            observation, create_cover_receipt("exact", "key"), "key"
        )


@pytest.mark.parametrize(
    "extra",
    [
        {"isbn": "9780000000000"},
        {"school_id": "school"},
        {"occurred_at": "2020-01-01"},
        {"schema_version": 2},
        {"cover_kind": "anything"},
    ],
)
def test_unknown_fields_and_versions_are_rejected(extra):
    with pytest.raises(ValidationError):
        CoverObservation.model_validate(
            {
                "event": "cover_presented",
                "schema_version": 1,
                "cover_kind": "exact",
                **extra,
            }
        )


def test_ingestion_rate_is_bounded_before_receipt_verification(monkeypatch):
    from app.services import browser_observations

    monkeypatch.setattr(browser_observations, "monotonic", lambda: 0)
    service = BrowserObservationService()
    observation = CoverObservation(
        event="cover_presented", schema_version=1, cover_kind="exact"
    )
    with patch.object(
        browser_observations.jwt, "decode", side_effect=ValueError
    ) as decode:
        for _ in range(browser_observations.MAX_REQUESTS_PER_SECOND):
            with pytest.raises(InvalidObservationReceipt):
                service.record(observation, "invalid", "key")
        service.record(observation, "invalid", "key")
        assert decode.call_count == browser_observations.MAX_REQUESTS_PER_SECOND
        monkeypatch.setattr(browser_observations, "monotonic", lambda: 1)
        with pytest.raises(InvalidObservationReceipt):
            service.record(observation, "invalid", "key")
        assert decode.call_count == browser_observations.MAX_REQUESTS_PER_SECOND + 1


def test_cover_receipt_expiry_and_signature(monkeypatch):
    from app.services import browser_observations

    observation = CoverObservation(
        event="cover_presented", schema_version=1, cover_kind="exact"
    )
    receipt = create_cover_receipt("exact", "key")
    with pytest.raises(InvalidObservationReceipt):
        BrowserObservationService().record(observation, receipt, "wrong-key")
    monkeypatch.setattr(browser_observations, "time", lambda: 0)
    expired = create_cover_receipt("exact", "key")
    with pytest.raises(InvalidObservationReceipt):
        BrowserObservationService().record(observation, expired, "key")
