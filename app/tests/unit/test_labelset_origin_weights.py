"""Guards for label-origin authority weighting.

The labelset patch logic indexes ORIGIN_WEIGHTS by every LabelOrigin it sees,
so a missing entry is a latent KeyError. It also relies on EDUCATOR sitting
above AI labels but below Wriveted staff (HUMAN) so teacher reviews improve
recommendations without overriding staff-confirmed labels.
"""

from app.models.labelset import LabelOrigin
from app.repositories.labelset_repository import ORIGIN_WEIGHTS

AI_ORIGINS = ["GPT4", "VERTEXAI"]


def test_every_label_origin_has_a_weight():
    missing = [o.name for o in LabelOrigin if o.name not in ORIGIN_WEIGHTS]
    assert not missing, f"LabelOrigin values without an ORIGIN_WEIGHTS entry: {missing}"


def test_educator_weight_sits_between_ai_and_staff():
    assert "EDUCATOR" in ORIGIN_WEIGHTS
    educator = ORIGIN_WEIGHTS["EDUCATOR"]
    assert educator < ORIGIN_WEIGHTS["HUMAN"], "educator must not override staff"
    for ai in AI_ORIGINS:
        assert educator > ORIGIN_WEIGHTS[ai], f"educator must outrank {ai}"
