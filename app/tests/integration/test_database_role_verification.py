from uuid import uuid4

import pytest
from sqlalchemy import text

from scripts.verify_database_roles import verify_database_roles


@pytest.fixture
def role_verification(session):
    suffix = uuid4().hex
    schema = f"role_verification_{suffix}"
    runtime_role = f"runtime_{suffix}"
    readonly_role = f"reader_{suffix}"
    inherited_role = f"inherited_{suffix}"
    connection = session.connection()
    for role in (runtime_role, readonly_role, inherited_role):
        connection.execute(text(f'CREATE ROLE "{role}"'))
    connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    connection.execute(text(f'CREATE TABLE "{schema}".books (id serial PRIMARY KEY)'))
    connection.execute(
        text(f'CREATE MATERIALIZED VIEW "{schema}".catalogue AS SELECT 1 AS id')
    )
    for role in (runtime_role, readonly_role):
        connection.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}"'))
        connection.execute(
            text(f'GRANT SELECT ON ALL TABLES IN SCHEMA "{schema}" TO "{role}"')
        )
        connection.execute(
            text(f'GRANT SELECT ON ALL SEQUENCES IN SCHEMA "{schema}" TO "{role}"')
        )
    connection.execute(
        text(f'GRANT INSERT, UPDATE, DELETE ON "{schema}".books TO "{runtime_role}"')
    )
    connection.execute(
        text(f'GRANT USAGE ON ALL SEQUENCES IN SCHEMA "{schema}" TO "{runtime_role}"')
    )
    connection.execute(text(f'GRANT "{inherited_role}" TO "{readonly_role}"'))
    yield (
        connection,
        dict(schema=schema, runtime_role=runtime_role, readonly_role=readonly_role),
        inherited_role,
    )
    session.rollback()


def test_expected_runtime_and_readonly_privileges_pass(role_verification):
    connection, roles, _ = role_verification
    verify_database_roles(connection, **roles)


@pytest.mark.parametrize(
    "statement, expected",
    [
        (
            'REVOKE INSERT ON "{schema}".books FROM "{runtime_role}"',
            "INSERT must be granted",
        ),
        (
            'REVOKE USAGE ON SCHEMA "{schema}" FROM "{runtime_role}"',
            "USAGE must be granted",
        ),
        (
            'REVOKE USAGE ON SCHEMA "{schema}" FROM "{readonly_role}"',
            "USAGE must be granted",
        ),
        (
            'REVOKE USAGE ON ALL SEQUENCES IN SCHEMA "{schema}" FROM "{runtime_role}"',
            "USAGE must be granted",
        ),
        (
            'REVOKE SELECT ON "{schema}".catalogue FROM "{readonly_role}"',
            "SELECT must be granted",
        ),
        (
            'GRANT INSERT ON "{schema}".books TO "{readonly_role}"',
            "INSERT must be denied",
        ),
        (
            'GRANT TRUNCATE ON "{schema}".books TO "{inherited_role}"',
            "TRUNCATE must be denied",
        ),
        (
            'GRANT UPDATE ON ALL SEQUENCES IN SCHEMA "{schema}" TO "{readonly_role}"',
            "UPDATE must be denied",
        ),
    ],
)
def test_privilege_drift_fails(role_verification, statement, expected):
    connection, roles, inherited_role = role_verification
    connection.execute(text(statement.format(**roles, inherited_role=inherited_role)))
    with pytest.raises(RuntimeError, match=expected):
        verify_database_roles(connection, **roles)
