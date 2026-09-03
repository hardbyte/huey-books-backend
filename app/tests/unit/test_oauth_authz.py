"""Unit tests for OAuth principal scoping (least privilege, cross-tenant safety)."""

from app.services.oauth.authz import build_oauth_principals

USER = "user-uuid"
# A user who is school admin at schools 12 AND 34.
REAL = {
    "role:reader",
    "role:educator",
    "role:schooladmin",
    "educator:12",
    "schooladmin:12",
    "educator:34",
    "schooladmin:34",
    f"user:{USER}",
}


def test_confined_to_granted_school_with_write():
    p = build_oauth_principals(USER, REAL, 12, {"books:label", "catalogue:read"})
    assert "schooladmin:12" in p and "educator:12" in p
    # No authority at the other school, even though the user is admin there.
    assert "schooladmin:34" not in p and "educator:34" not in p


def test_read_only_scope_drops_write_principal():
    p = build_oauth_principals(USER, REAL, 12, {"catalogue:read"})
    assert "educator:12" in p
    assert "schooladmin:12" not in p  # no write scope -> no full access


def test_no_membership_at_granted_school_grants_no_school_principal():
    p = build_oauth_principals(USER, REAL, 99, {"books:label"})
    assert "schooladmin:99" not in p and "educator:99" not in p
    # Global catalogue-read roles still carried.
    assert "role:reader" in p and "role:educator" in p
    assert f"user:{USER}" in p
