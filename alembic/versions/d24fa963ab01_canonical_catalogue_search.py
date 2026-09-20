"""Canonical catalogue search documents.

Revision ID: d24fa963ab01
Revises: c13ed852fa90
"""

from sqlalchemy import text

from alembic import op

revision = "d24fa963ab01"
down_revision = "c13ed852fa90"
branch_labels = None
depends_on = None

NEW_DEFINITION = """
SELECT w.id AS work_id,
       coalesce(authors.author_ids, '[]'::jsonb) AS author_ids,
       NULL::integer AS series_id,
       setweight(to_tsvector('english', coalesce(w.title, '')), 'A') ||
       setweight(to_tsvector('english', coalesce(w.subtitle, '')), 'C') ||
       setweight(to_tsvector('english', coalesce(authors.names, '')), 'C') ||
       setweight(to_tsvector('english', coalesce(series.titles, '')), 'B') AS document
FROM public.works w
LEFT JOIN (
    SELECT awa.work_id,
           jsonb_agg(a.id ORDER BY a.id) AS author_ids,
           string_agg(concat_ws(' ', a.first_name, a.last_name), ' ' ORDER BY a.id) AS names
    FROM public.author_work_association awa
    JOIN public.authors a ON a.id = awa.author_id
    GROUP BY awa.work_id
) authors ON authors.work_id = w.id
LEFT JOIN (
    SELECT swa.work_id, string_agg(s.title, ' ' ORDER BY s.id) AS titles
    FROM public.series_works_association swa
    JOIN public.series s ON s.id = swa.series_id
    GROUP BY swa.work_id
) series ON series.work_id = w.id
"""

OLD_DEFINITION = """
SELECT w.id AS work_id,
       jsonb_agg(a.id) AS author_ids,
       s.id as series_id,
       setweight(to_tsvector('english', coalesce(w.title, '')), 'A') ||
       setweight(to_tsvector('english', coalesce(w.subtitle, '')), 'C') ||
       setweight(to_tsvector('english', (SELECT string_agg(coalesce(first_name || ' ' || last_name, ''), ' ') FROM public.authors WHERE id IN (SELECT author_id FROM public.author_work_association WHERE work_id = w.id))), 'C') ||
       setweight(to_tsvector('english', coalesce(s.title, '')), 'B')
                                          AS document
FROM public.works w
         JOIN
     public.author_work_association awa ON awa.work_id = w.id
         JOIN
     public.authors a ON a.id = awa.author_id
LEFT JOIN
    public.series_works_association swa ON swa.work_id = w.id
LEFT JOIN
    public.series s ON s.id = swa.series_id
GROUP BY
    w.id, s.id
"""


def replace_view(definition: str, canonical: bool) -> None:
    connection = op.get_bind()
    quote = connection.dialect.identifier_preparer.quote
    owner = connection.scalar(
        text(
            "SELECT pg_get_userbyid(relowner) FROM pg_class "
            "WHERE oid = 'public.search_view_v1'::regclass"
        )
    )
    grants = (
        connection.execute(
            text("""
        SELECT grantee, privilege_type, is_grantable,
               CASE WHEN grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(grantee) END AS role
        FROM pg_class, LATERAL aclexplode(coalesce(relacl, acldefault('r', relowner)))
        WHERE oid = 'public.search_view_v1'::regclass
    """)
        )
        .mappings()
        .all()
    )
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '120s'")
    op.execute(
        "CREATE MATERIALIZED VIEW public.search_view_replacement AS " + definition
    )
    op.execute(
        "CREATE INDEX ix_search_replacement_document ON public.search_view_replacement USING gin (document)"
    )
    columns = "work_id" if canonical else "work_id, series_id"
    nulls = "" if canonical else " NULLS NOT DISTINCT"
    op.execute(
        f"CREATE UNIQUE INDEX uix_search_replacement ON public.search_view_replacement ({columns}){nulls}"
    )
    default_roles = (
        connection.execute(
            text("""
        SELECT DISTINCT CASE WHEN grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(grantee) END AS role,
                        grantee = 0 AS is_public
        FROM pg_class, LATERAL aclexplode(coalesce(relacl, acldefault('r', relowner)))
        WHERE oid = 'public.search_view_replacement'::regclass
    """)
        )
        .mappings()
        .all()
    )
    for role in default_roles:
        target = "PUBLIC" if role["is_public"] else quote(role["role"])
        op.execute(f"REVOKE ALL ON public.search_view_replacement FROM {target}")
    for grant in grants:
        target = "PUBLIC" if grant["grantee"] == 0 else quote(grant["role"])
        option = " WITH GRANT OPTION" if grant["is_grantable"] else ""
        op.execute(
            f"GRANT {grant['privilege_type']} ON public.search_view_replacement TO {target}{option}"
        )
    op.execute(
        f"ALTER MATERIALIZED VIEW public.search_view_replacement OWNER TO {quote(owner)}"
    )
    op.execute("DROP MATERIALIZED VIEW public.search_view_v1")
    op.execute(
        "ALTER MATERIALIZED VIEW public.search_view_replacement RENAME TO search_view_v1"
    )
    op.execute(
        "ALTER INDEX public.ix_search_replacement_document RENAME TO ix_search_document"
    )
    unique_name = (
        "uix_search_view_work_id" if canonical else "uix_search_view_work_series"
    )
    op.execute(f"ALTER INDEX public.uix_search_replacement RENAME TO {unique_name}")
    op.execute("""
        INSERT INTO public.search_index_refreshes(index_name, source_snapshot_at, refreshed_at)
        VALUES ('search_view_v1', transaction_timestamp(), clock_timestamp())
        ON CONFLICT (index_name) DO UPDATE
        SET source_snapshot_at = EXCLUDED.source_snapshot_at, refreshed_at = EXCLUDED.refreshed_at
    """)
    op.execute("ANALYZE public.search_view_v1")


def upgrade() -> None:
    replace_view(NEW_DEFINITION, canonical=True)


def downgrade() -> None:
    replace_view(OLD_DEFINITION, canonical=False)
