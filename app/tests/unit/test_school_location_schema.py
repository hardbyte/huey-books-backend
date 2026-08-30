"""Regression tests for SchoolLocation response serialization.

Location data varies by source: coordinates may be stored numerically or as
strings, and postcode/state may be absent. The response schema must tolerate all
of these rather than 500 on serialization.
"""

from app.schemas.school import SchoolInfo, SchoolLocation


def test_location_coerces_numeric_lat_long_and_allows_null_postcode():
    loc = SchoolLocation.model_validate(
        {"state": "Somewhere", "postcode": None, "lat": 11.412514, "long": 76.70738}
    )
    assert loc.lat == "11.412514"
    assert loc.long == "76.70738"
    assert loc.postcode is None
    assert loc.state == "Somewhere"


def test_location_allows_all_fields_absent():
    loc = SchoolLocation.model_validate({})
    assert loc.state is None
    assert loc.postcode is None
    assert loc.lat is None


def test_school_info_serializes_numeric_coordinates():
    info = SchoolInfo.model_validate(
        {"location": {"lat": 13.13848, "long": 79.762242}, "type": "International"}
    )
    dumped = info.model_dump(mode="json")
    assert dumped["location"]["lat"] == "13.13848"
    assert dumped["location"]["postcode"] is None


def test_location_still_accepts_string_lat_long():
    loc = SchoolLocation.model_validate(
        {"state": "VIC", "postcode": "3000", "lat": "-37.8", "long": "144.9"}
    )
    assert loc.lat == "-37.8"
    assert loc.postcode == "3000"
