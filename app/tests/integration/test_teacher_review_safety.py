from unittest.mock import patch
from uuid import uuid4

from app.models.labelset import LabelOrigin, LabelSet, RecommendStatus
from app.models.work import Work, WorkType
from app.repositories.labelset_repository import labelset_repository
from app.repositories.review_repository import review_repository
from app.schemas.labelset import LabelSetCreateIn
from scripts.reclassify_reviewed_ai_labels import reclassify


def test_confirm_multiple_existing_levels_preserves_labels(
    client, session, test_schooladmin_account_headers
):
    work = Work(title="Multi-level confirmation fixture", type=WorkType.BOOK)
    session.add(work)
    session.flush()
    labels = labelset_repository.get_or_create(session, work, commit=True)
    labelset_repository.patch(
        session,
        labels,
        LabelSetCreateIn(
            hue_primary_key="hue01_dark_suspense",
            hue_origin=LabelOrigin.HUMAN,
            min_age=3,
            max_age=8,
            age_origin=LabelOrigin.HUMAN,
            reading_ability_keys=["SPOT", "CHARLIE_CHOCOLATE"],
            reading_ability_origin=LabelOrigin.HUMAN,
            recommend_status=RecommendStatus.GOOD,
            recommend_status_origin=LabelOrigin.HUMAN,
            checked=True,
        ),
        commit=True,
    )
    proposal = {
        "hue_primary_key": "hue01_dark_suspense",
        "min_age": 3,
        "max_age": 8,
        "recommend_status": "GOOD",
        "confirmed_existing": True,
        "expected_reading_ability_keys": ["CHARLIE_CHOCOLATE", "SPOT"],
    }
    path = f"/v1/work/{work.id}/reviews"
    with patch("app.api.reviews.enqueue_debounced_mv_refresh"):
        response = client.post(
            path, headers=test_schooladmin_account_headers, json=proposal
        )
    assert response.status_code == 200, response.text
    assert response.json()["expected_reading_ability_keys"] == [
        "CHARLIE_CHOCOLATE",
        "SPOT",
    ]
    session.refresh(labels)
    assert set(labels.get_label_dict(session)["reading_ability_keys"]) == {
        "CHARLIE_CHOCOLATE",
        "SPOT",
    }
    assert labels.reading_ability_origin == LabelOrigin.HUMAN
    assert labels.checked is True
    stale = client.post(
        path,
        headers=test_schooladmin_account_headers,
        json={**proposal, "expected_reading_ability_keys": ["SPOT"]},
    )
    assert stale.status_code == 409, stale.text
    ambiguous = client.post(
        path,
        headers=test_schooladmin_account_headers,
        json={**proposal, "reading_ability_key": "SPOT"},
    )
    assert ambiguous.status_code == 422, ambiguous.text


def test_teacher_review_authority_and_provenance(
    client, session, test_schooladmin_account_headers
):
    work = Work(title="Teacher review safety fixture", type=WorkType.BOOK)
    session.add(work)
    session.flush()
    labels = LabelSet(
        work_id=work.id,
        checked=None,
        min_age=3,
        max_age=5,
        recommend_status=RecommendStatus.BAD_CONTROVERSIAL,
        recommend_status_origin=LabelOrigin.HUMAN,
        info={"private_audit_fixture": "must-not-enter-external-prompt"},
    )
    session.add(labels)
    session.commit()
    headers = test_schooladmin_account_headers
    path = f"/v1/work/{work.id}"
    assert (
        client.patch(
            path,
            headers=headers,
            json={
                "labelset": {
                    "hue_origin": "HUMAN",
                    "hue_primary_key": "hue04_joyful_charming",
                }
            },
        ).status_code
        == 403
    )
    assert client.delete(path, headers=headers).status_code == 403
    prompt = client.get(path + "/labelling-prompt", headers=headers)
    assert prompt.status_code == 200, prompt.text
    assert "teacher-review-v1" in prompt.json()["system_prompt"]
    assert "must-not-enter-external-prompt" not in prompt.json()["user_prompt"]
    proposal = {
        "hue_primary_key": "hue04_joyful_charming",
        "min_age": 3,
        "max_age": 5,
        "reading_ability_key": "SPOT",
        "recommend_status": "GOOD",
        "ai_assistance": {
            "model": "test-model-v1",
            "sources": ["https://example.org/book"],
            "uncertainties": "Edition not read in full",
        },
    }
    with patch("app.api.reviews.enqueue_debounced_mv_refresh"):
        response = client.post(path + "/reviews", headers=headers, json=proposal)
    assert response.status_code == 200, response.text
    assert response.json()["ai_assistance"]["model"] == "test-model-v1"
    session.refresh(labels)
    assert labels.checked is None
    assert labels.hue_origin == LabelOrigin.EDUCATOR
    assert labels.reading_ability_origin == LabelOrigin.EDUCATOR
    assert labels.recommend_status == RecommendStatus.BAD_CONTROVERSIAL
    invalid_confirmation = client.post(
        path + "/reviews",
        headers=headers,
        json={**proposal, "confirmed_existing": True},
    )
    assert invalid_confirmation.status_code == 409
    assert (
        client.post(
            path + "/reviews", headers=headers, json={"min_age": 8, "max_age": 2}
        ).status_code
        == 422
    )


async def test_backfill_is_scoped_idempotent_and_preserves_evidence(
    async_session, monkeypatch
):
    session = async_session
    run = "test-ai-" + uuid4().hex
    monkeypatch.setattr(
        "scripts.reclassify_reviewed_ai_labels.RUNS", (run, "unused-test-run")
    )
    work = Work(title="Audited AI fixture", type=WorkType.BOOK)
    unrelated_work = Work(title="Unrelated OTHER fixture", type=WorkType.BOOK)
    session.add_all([work, unrelated_work])
    await session.flush()
    provenance = {
        "run": run,
        "research_model": "gpt-5.6-terra",
        "human_reviewed": False,
        "before": {"min_age": 12, "max_age": 16, "huey_summary": "Existing summary"},
        "proposal": {"sources": [{"url": "https://example.org/book"}]},
    }
    labels = LabelSet(
        work_id=work.id,
        hue_origin=LabelOrigin.OTHER,
        reading_ability_origin=LabelOrigin.OTHER,
        age_origin=LabelOrigin.HUMAN,
        min_age=12,
        max_age=16,
        checked=None,
        recommend_status=RecommendStatus.BAD_CONTROVERSIAL,
        recommend_status_origin=LabelOrigin.HUMAN,
        info={"reviewed_ai_labelling": provenance},
    )
    unrelated = LabelSet(
        work_id=unrelated_work.id, hue_origin=LabelOrigin.OTHER, checked=None
    )
    session.add_all([labels, unrelated])
    await session.commit()
    assert len(await session.run_sync(reclassify, expected_count=1)) == 1
    await session.flush()
    await session.refresh(labels)
    await session.refresh(unrelated)
    assert labels.hue_origin == LabelOrigin.AI_ASSISTED
    assert labels.reading_ability_origin == LabelOrigin.AI_ASSISTED
    assert labels.info == {"reviewed_ai_labelling": provenance}
    assert (labels.min_age, labels.max_age, labels.age_origin, labels.checked) == (
        12,
        16,
        LabelOrigin.HUMAN,
        None,
    )
    assert labels.recommend_status == RecommendStatus.BAD_CONTROVERSIAL
    assert unrelated.hue_origin == LabelOrigin.OTHER
    assert await session.run_sync(reclassify, expected_count=1) == []
    items, _ = await review_repository.get_review_queue(
        db=session, status="ai_labelled", limit=100000
    )
    assert work.id in [item["work_id"] for item in items]
    await session.rollback()
