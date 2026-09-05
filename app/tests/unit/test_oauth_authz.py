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


def test_wriveted_admin_granted_any_school_confined_to_that_school():
    admin = {"role:admin", f"user:{USER}"}
    p = build_oauth_principals(USER, admin, 99, {"books:label", "catalogue:read"})
    # Admin gets full access at the granted school even without explicit membership.
    assert "schooladmin:99" in p and "educator:99" in p
    # But is still confined to that one school.
    assert "schooladmin:12" not in p and "educator:12" not in p


def test_wriveted_admin_read_only_scope_drops_write():
    admin = {"role:admin", f"user:{USER}"}
    p = build_oauth_principals(USER, admin, 99, {"catalogue:read"})
    assert "educator:99" in p
    assert "schooladmin:99" not in p


def test_global_educator_role_gated_by_write_scope():
    # role:educator grants All on works (a write), so a read-only token must not
    # carry it, even though the user holds it.
    read = build_oauth_principals(USER, REAL, 12, {"catalogue:read"})
    assert "role:educator" not in read
    write = build_oauth_principals(USER, REAL, 12, {"books:label"})
    assert "role:educator" in write


def test_role_admin_is_never_carried_but_admin_can_label():
    admin = {"role:admin", f"user:{USER}"}
    p = build_oauth_principals(USER, admin, 99, {"books:label"})
    assert "role:admin" not in p  # never grant All-everywhere
    assert "role:educator" in p  # but admins may edit/label works with write scope


def test_read_only_never_carries_write_capable_roles():
    p = build_oauth_principals(USER, REAL, 12, {"catalogue:read"})
    assert "role:reader" in p  # read-only role is fine
    assert "role:educator" not in p and "role:admin" not in p
