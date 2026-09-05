from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.api.reviews import _promote_review_to_canonical
from app.api.works import bulk_work_access_control_list
from app.models.labelset import LabelOrigin
from app.repositories.labelset_repository import labelset_repository
from app.repositories.review_repository import AI_ORIGINS
from app.schemas.labelset import LabelSetCreateIn
from app.schemas.review import LabelSetReviewIn


def test_teacher_promotion_preserves_unchecked():
    labelset = SimpleNamespace(checked=None)
    with patch("app.api.reviews.labelset_repository.patch") as update:
        _promote_review_to_canonical(
            MagicMock(),
            labelset,
            LabelSetReviewIn(min_age=3, max_age=5),
            SimpleNamespace(id=uuid4()),
            origin=LabelOrigin.EDUCATOR,
            mark_checked=False,
        )
    assert update.call_args.args[2].checked is None


def test_educators_cannot_directly_modify_shared_catalogue():
    assert not any(rule[1] == "role:educator" for rule in bulk_work_access_control_list)


def test_ai_assisted_is_in_review_queue():
    assert LabelOrigin.AI_ASSISTED in AI_ORIGINS


def test_ai_metadata_patch_preserves_existing_evidence_and_checked_flag():
    labelset = SimpleNamespace(info={"legacy": {"evidence": "preserved"}}, checked=True)
    data = LabelSetCreateIn(
        info={
            "ai_assistance": {
                "model": "example-model-v1",
                "sources": ["https://example.org/book"],
            }
        }
    )
    labelset_repository.patch(MagicMock(), labelset, data)
    assert labelset.checked is True
    assert labelset.info["legacy"] == {"evidence": "preserved"}
    assert labelset.info["ai_assistance"]["schema_version"] == 1
    assert labelset.info["ai_assistance"]["model"] == "example-model-v1"


def test_ai_metadata_rejects_unsafe_source_urls():
    with pytest.raises(ValidationError):
        LabelSetCreateIn(info={"ai_assistance": {"sources": ["javascript:alert(1)"]}})


@pytest.mark.parametrize(
    "values",
    [
        {"min_age": 8, "max_age": 4},
        {"min_age": -1},
        {"max_age": 101},
        {"hue_primary_key": "invented"},
        {"reading_ability_key": "invented"},
    ],
)
def test_invalid_review_labels_rejected(values):
    with pytest.raises(ValidationError):
        LabelSetReviewIn(**values)
