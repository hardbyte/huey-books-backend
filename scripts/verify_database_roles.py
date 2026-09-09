import os

from sqlalchemy import create_engine, text


def verify_database_roles(connection) -> None:
    failures = connection.execute(
        text("""
        WITH expected(role_name, privilege, allowed, relation_kinds) AS (
            VALUES
                ('cloudrun', 'SELECT', true, 'rpm'),
                ('cloudrun', 'INSERT', true, 'rp'),
                ('cloudrun', 'UPDATE', true, 'rp'),
                ('cloudrun', 'DELETE', true, 'rp'),
                ('readonly', 'SELECT', true, 'rpm'),
                ('readonly', 'INSERT', false, 'rpm'),
                ('readonly', 'UPDATE', false, 'rpm'),
                ('readonly', 'DELETE', false, 'rpm')
        )
        SELECT expected.role_name, relations.relname, expected.privilege,
               expected.allowed
        FROM pg_class AS relations
        JOIN pg_namespace AS namespaces ON namespaces.oid = relations.relnamespace
        CROSS JOIN expected
        WHERE namespaces.nspname = 'public'
          AND strpos(expected.relation_kinds, relations.relkind::text) > 0
          AND has_table_privilege(expected.role_name, relations.oid,
                                  expected.privilege) IS DISTINCT FROM expected.allowed
        ORDER BY expected.role_name, relations.relname, expected.privilege
    """)
    ).all()
    if failures:
        details = "; ".join(
            f"{role} on public.{relation}: {privilege} must be {'granted' if allowed else 'denied'}"
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
