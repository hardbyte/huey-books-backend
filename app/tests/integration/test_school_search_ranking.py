"""Ranked, typo-tolerant school-name search.

Exercises the pg_trgm-backed search added to the /v1/schools endpoint: a clean
partial should surface the school, a minor typo should still recall it, and the
closest match should rank first.
"""

import secrets

import pytest

from app.repositories.school_repository import school_repository

# A rare, made-up stem so the assertions are not polluted by real/seeded
# schools that happen to share common words.
STEM = "qwzzlebrook"


def _create_school(client, headers, name: str) -> str:
    official_id = secrets.token_hex(8)
    response = client.post(
        "/v1/school",
        headers=headers,
        json={
            "name": name,
            "country_code": "ATA",
            "official_identifier": official_id,
            "info": {
                "msg": "Created for search-ranking test",
                "location": {"state": "Required", "postcode": "Required"},
            },
        },
        timeout=120,
    )
    response.raise_for_status()
    return response.json()["wriveted_identifier"]


@pytest.fixture()
def search_schools(client, session, backend_service_account_headers):
    """Create a target school plus a longer one sharing the rare stem."""
    target_id = _create_school(
        client, backend_service_account_headers, f"{STEM} School"
    )
    longer_id = _create_school(
        client,
        backend_service_account_headers,
        f"North {STEM} Catholic College Senior Campus",
    )
    created = {"target": target_id, "longer": longer_id}
    yield created

    session.rollback()
    for wriveted_id in created.values():
        school = school_repository.get_by_wriveted_id(
            db=session, wriveted_id=wriveted_id
        )
        if school is not None:
            school_repository.remove(db=session, obj_in=school)


def _names(response) -> list[str]:
    response.raise_for_status()
    return [s["name"] for s in response.json()]


def test_exact_partial_ranks_closest_match_first(
    client, backend_service_account_headers, search_schools
):
    """An exact stem ranks the short "<stem> School" above the longer name."""
    names = _names(
        client.get(
            f"/v1/schools?q={STEM}&limit=20", headers=backend_service_account_headers
        )
    )
    assert f"{STEM} School" in names
    assert f"North {STEM} Catholic College Senior Campus" in names
    # Prefix match outranks a mid-string match.
    assert names.index(f"{STEM} School") < names.index(
        f"North {STEM} Catholic College Senior Campus"
    )


def test_prefix_fragment_recalls_school(
    client, backend_service_account_headers, search_schools
):
    """A short prefix fragment (the original "Somer" failure) still matches."""
    names = _names(
        client.get(
            f"/v1/schools?q={STEM[:5]}&limit=20",
            headers=backend_service_account_headers,
        )
    )
    assert f"{STEM} School" in names


def test_typo_is_tolerated(client, backend_service_account_headers, search_schools):
    """A single-character deletion still recalls the school and ranks it first."""
    typo = STEM.replace("zz", "z")  # qwzzlebrook -> qwzlebrook
    names = _names(
        client.get(
            f"/v1/schools?q={typo}&limit=20", headers=backend_service_account_headers
        )
    )
    assert f"{STEM} School" in names
    assert names[0] == f"{STEM} School"
