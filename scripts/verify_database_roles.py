import os

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection


def verify_database_roles(
    connection: Connection,
    *,
    runtime_role: str = "cloudrun",
    readonly_role: str = "readonly",
    schema: str = "public",
) -> None:
    failures = connection.execute(
        text("""
        WITH expected(role_name, privilege, allowed, relation_kinds) AS (
            VALUES
                (:runtime_role, 'SELECT', true, 'rpm'),
                (:runtime_role, 'INSERT', true, 'rp'),
                (:runtime_role, 'UPDATE', true, 'rp'),
                (:runtime_role, 'DELETE', true, 'rp'),
                (:readonly_role, 'SELECT', true, 'rpm'),
                (:readonly_role, 'INSERT', false, 'rpm'),
                (:readonly_role, 'UPDATE', false, 'rpm'),
                (:readonly_role, 'DELETE', false, 'rpm'),
                (:readonly_role, 'TRUNCATE', false, 'rpm'),
                (:readonly_role, 'REFERENCES', false, 'rpm'),
                (:readonly_role, 'TRIGGER', false, 'rpm')
        )
        SELECT expected.role_name, relations.relname, expected.privilege,
               expected.allowed
        FROM pg_class AS relations
        JOIN pg_namespace AS namespaces ON namespaces.oid = relations.relnamespace
        CROSS JOIN expected
        WHERE namespaces.nspname = :schema
          AND strpos(expected.relation_kinds, relations.relkind::text) > 0
          AND has_table_privilege(expected.role_name, relations.oid,
                                  expected.privilege) IS DISTINCT FROM expected.allowed
        UNION ALL
        SELECT role_name, :schema, 'USAGE', true
        FROM (VALUES (:runtime_role), (:readonly_role)) AS roles(role_name)
        WHERE NOT has_schema_privilege(role_name, :schema, 'USAGE')
        UNION ALL
        SELECT role_name, sequences.relname, privilege, allowed
        FROM pg_class AS sequences
        JOIN pg_namespace AS namespaces ON namespaces.oid = sequences.relnamespace
        CROSS JOIN (VALUES
            (:runtime_role, 'USAGE', true),
            (:runtime_role, 'SELECT', true),
            (:readonly_role, 'SELECT', true),
            (:readonly_role, 'USAGE', false),
            (:readonly_role, 'UPDATE', false)
        ) AS expected_sequences(role_name, privilege, allowed)
        WHERE namespaces.nspname = :schema AND sequences.relkind = 'S'
          AND has_sequence_privilege(role_name, sequences.oid, privilege)
              IS DISTINCT FROM allowed
        ORDER BY 1, 2, 3
    """),
        {
            "runtime_role": runtime_role,
            "readonly_role": readonly_role,
            "schema": schema,
        },
    ).all()
    if failures:
        details = "; ".join(
            f"{role} on {schema}.{relation}: {privilege} must be {'granted' if allowed else 'denied'}"
            for role, relation, privilege, allowed in failures
        )
        raise RuntimeError(f"Database runtime role verification failed: {details}")


if __name__ == "__main__":
    engine = create_engine(os.environ["DATABASE_URL"])
    try:
        with engine.connect() as connection:
            verify_database_roles(connection)
        print("Database runtime role privileges verified")
    finally:
        engine.dispose()
