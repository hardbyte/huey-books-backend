"""Map an OAuth token to a least-privilege principal set.

An OAuth access token authorises one user acting at ONE school with a set of
scopes. Rather than granting the user's full principals (all their schools, all
roles), we build a confined set: shared-catalogue read roles the user already
holds, plus only the granted school's principals, with a coarse read/write gate
from the scopes. This reuses the existing RBAC ACLs unchanged and makes a
cross-school action impossible even if the user is staff at several schools.

Deny-by-default: only the principals listed here are granted.
"""

from __future__ import annotations

from fastapi_permissions import Authenticated, Everyone

# Scopes that permit writes (collection import / labelling).
_WRITE_SCOPES = {"books:import", "books:label"}
# Genuinely read-only global role — safe to carry for a read scope.
_READ_ONLY_ROLES = ("role:reader",)


def build_oauth_principals(
    user_id, real_principals: set[str], school_id_int: int, scopes: set[str]
) -> list:
    """Confine ``real_principals`` to the granted school, gated by ``scopes``."""
    scoped: list = [Everyone, Authenticated, f"user:{user_id}"]

    write = bool(scopes & _WRITE_SCOPES)
    is_admin = "role:admin" in real_principals

    for role in _READ_ONLY_ROLES:
        if role in real_principals:
            scoped.append(role)
    # role:educator grants catalogue WRITES (labelling works has All on works), so
    # only carry it for a write-scoped token; admins may label too. role:admin
    # itself is never carried — that would be All authority everywhere.
    if write and ("role:educator" in real_principals or is_admin):
        scoped.append("role:educator")
    educator = f"educator:{school_id_int}"
    schooladmin = f"schooladmin:{school_id_int}"
    # A Wriveted admin may act at any school; still confined to the ONE granted
    # school here so an OAuth token can never span schools.
    is_admin = "role:admin" in real_principals
    # Read access to the granted school (only if the user actually holds it).
    if educator in real_principals or is_admin:
        scoped.append(educator)
    # Full (write) access to the granted school only when a write scope is present.
    if (schooladmin in real_principals or is_admin) and write:
        scoped.append(schooladmin)

    return scoped
