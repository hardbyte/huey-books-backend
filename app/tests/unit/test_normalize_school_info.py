"""Tests for the write-side School.info normaliser.

School.info is untyped JSONB written by several sources that store the
location block's coordinates inconsistently (lat/long as numbers or strings).
normalize_school_info coerces coordinates to the canonical string shape while
preserving every other key.
"""

from app.schemas.school import normalize_school_info


def test_numeric_coordinates_coerced_to_strings():
    result = normalize_school_info({"location": {"lat": -37.8136, "long": 144.9631}})
    assert result["location"]["lat"] == "-37.8136"
    assert result["location"]["long"] == "144.9631"


def test_string_coordinates_left_unchanged():
    result = normalize_school_info({"location": {"lat": "-37.8", "long": "144.9"}})
    assert result["location"]["lat"] == "-37.8"
    assert result["location"]["long"] == "144.9"


def test_null_and_absent_fields_tolerated():
    result = normalize_school_info({"location": {"state": "VIC", "postcode": None}})
    assert result["location"]["state"] == "VIC"
    assert result["location"]["postcode"] is None
    assert result["location"]["lat"] is None


def test_other_info_keys_preserved():
    result = normalize_school_info(
        {
            "location": {"lat": 1.5, "long": 2.5},
            "type": "International",
            "sector": "Independent",
            "URL": "https://example.test",
            "status": "open",
            "age_id": "abc",
            "experiments": {"feature": True},
            "terms_acceptance": {"version": "1"},
            "source": "some_import",
            "onboarding": {"contact_name": "Jo"},
        }
    )
    assert result["type"] == "International"
    assert result["sector"] == "Independent"
    assert result["URL"] == "https://example.test"
    assert result["status"] == "open"
    assert result["age_id"] == "abc"
    assert result["experiments"] == {"feature": True}
    assert result["terms_acceptance"] == {"version": "1"}
    assert result["source"] == "some_import"
    assert result["onboarding"] == {"contact_name": "Jo"}
    assert result["location"]["lat"] == "1.5"


def test_unmodelled_location_keys_preserved():
    result = normalize_school_info(
        {"location": {"lat": 1.0, "district": "Central", "city": "Metropolis"}}
    )
    assert result["location"]["district"] == "Central"
    assert result["location"]["city"] == "Metropolis"
    assert result["location"]["lat"] == "1.0"


def test_missing_or_non_dict_location_returns_input():
    assert normalize_school_info({"type": "x"}) == {"type": "x"}
    assert normalize_school_info({"location": None}) == {"location": None}


def test_none_and_non_dict_info_returns_input():
    assert normalize_school_info(None) is None
    assert normalize_school_info("not a dict") == "not a dict"
