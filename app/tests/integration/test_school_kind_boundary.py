"""A schools row created as a library is not an education unit.

The organisation workspace still stores libraries in `schools`, so the
separation between a library and an education unit has to be enforced by the
database rather than by callers. See docs/organisation-target-schema.md for the
identities these rows stand in for.
"""

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, InternalError

from app.models.school import SchoolKind

REJECTED = (IntegrityError, InternalError)


@pytest.fixture
def rows(session):
    """Two schools rows differing only in kind, rolled back afterwards."""
    connection = session.connection()
    transaction = connection.begin_nested()
    created = {}
    for kind in (SchoolKind.SCHOOL, SchoolKind.LIBRARY):
        identifier = uuid4()
        connection.execute(
            text(
                "INSERT INTO schools (name, wriveted_identifier, state, kind,"
                " bookbot_type, lms_type) VALUES (:name, :uuid, 'INACTIVE', :kind,"
                " 'HUEY_BOOKS', 'none')"
            ),
            {
                "name": f"{kind.value}-{identifier}",
                "uuid": identifier,
                "kind": kind.value,
            },
        )
        row_id = connection.scalar(
            text("SELECT id FROM schools WHERE wriveted_identifier = :uuid"),
            {"uuid": identifier},
        )
        created[kind] = (row_id, identifier)
    try:
        yield created
    finally:
        transaction.rollback()


def attempt(session, statement, params):
    savepoint = session.connection().begin_nested()
    try:
        session.connection().execute(text(statement), params)
    except REJECTED as exc:
        savepoint.rollback()
        return exc
    savepoint.rollback()
    return None


def test_a_library_row_cannot_hold_admission_or_official_identity(session, rows):
    school_id, _ = rows[SchoolKind.SCHOOL]
    library_id, _ = rows[SchoolKind.LIBRARY]
    for column in ("student_domain", "teacher_domain", "official_identifier"):
        statement = f"UPDATE schools SET {column} = 'value' WHERE id = :id"
        assert attempt(session, statement, {"id": library_id}) is not None, column
        assert attempt(session, statement, {"id": school_id}) is None, column


def test_a_library_row_cannot_hold_classes(session, rows):
    _, school_uuid = rows[SchoolKind.SCHOOL]
    _, library_uuid = rows[SchoolKind.LIBRARY]
    statement = (
        "INSERT INTO class_groups (id, name, school_id)"
        " VALUES (gen_random_uuid(), :name, :uuid)"
    )
    assert attempt(session, statement, {"name": "Room 1", "uuid": library_uuid})
    assert attempt(session, statement, {"name": "Room 1", "uuid": school_uuid}) is None


def test_a_library_row_cannot_be_a_home_school(session, rows):
    school_id, _ = rows[SchoolKind.SCHOOL]
    library_id, _ = rows[SchoolKind.LIBRARY]
    statement = (
        "WITH created AS ("
        " INSERT INTO users (id, is_active, name, email, type, created_at, updated_at)"
        " VALUES (gen_random_uuid(), true, 'Staff', :email, 'EDUCATOR', now(), now())"
        " RETURNING id)"
        " INSERT INTO educators (id, school_id) SELECT id, :school FROM created"
    )
    assert attempt(
        session, statement, {"email": f"{uuid4()}@example.com", "school": library_id}
    )
    assert (
        attempt(
            session, statement, {"email": f"{uuid4()}@example.com", "school": school_id}
        )
        is None
    )
    # Staff with no home school stay valid; the guard only rejects library rows.
    assert (
        attempt(
            session,
            statement.replace(":school", "NULL"),
            {"email": f"{uuid4()}@example.com"},
        )
        is None
    )


def test_an_existing_class_cannot_be_moved_to_a_library_row(session, rows):
    _, school_uuid = rows[SchoolKind.SCHOOL]
    _, library_uuid = rows[SchoolKind.LIBRARY]
    name = f"Room {uuid4()}"
    session.connection().execute(
        text(
            "INSERT INTO class_groups (id, name, school_id)"
            " VALUES (gen_random_uuid(), :name, :uuid)"
        ),
        {"name": name, "uuid": school_uuid},
    )
    assert attempt(
        session,
        "UPDATE class_groups SET school_id = :uuid WHERE name = :name",
        {"uuid": library_uuid, "name": name},
    )
