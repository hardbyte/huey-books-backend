from unittest.mock import patch
from uuid import uuid4

from app.models.labelset import LabelOrigin, LabelSet, RecommendStatus
from app.models.work import Work, WorkType
from app.repositories.review_repository import review_repository
from scripts.reclassify_reviewed_ai_labels import reclassify


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


def test_backfill_is_scoped_idempotent_and_preserves_evidence(session, monkeypatch):
    run = "test-ai-" + uuid4().hex
    monkeypatch.setattr(
        "scripts.reclassify_reviewed_ai_labels.RUNS", (run, "unused-test-run")
    )
    work = Work(title="Audited AI fixture", type=WorkType.BOOK)
    unrelated_work = Work(title="Unrelated OTHER fixture", type=WorkType.BOOK)
    session.add_all([work, unrelated_work])
    session.flush()
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
    session.commit()
    assert len(reclassify(session, expected_count=1)) == 1
    session.flush()
    session.refresh(labels)
    session.refresh(unrelated)
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
    assert reclassify(session, expected_count=1) == []
    items, _ = review_repository.get_review_queue(
        db=session, status="ai_labelled", limit=100000
    )
    assert work.id in [item["work_id"] for item in items]
    session.rollback()
