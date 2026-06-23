from alembic_utils.pg_materialized_view import PGMaterializedView

recommendable_editions_view = PGMaterializedView(
    schema="public",
    signature="recommendable_editions",
    definition="""
SELECT
    ls.work_id,
    ls.id                         AS labelset_id,
    ls.min_age,
    ls.max_age,
    ls.recommend_status,
    cover_ed.isbn                 AS cover_edition_isbn,
    cover_ed.cover_url,
    COALESCE(
        array_agg(DISTINCT h.key) FILTER (WHERE h.key IS NOT NULL),
        '{}'
    )                             AS hue_keys,
    COALESCE(
        array_agg(DISTINCT ra.key) FILTER (WHERE ra.key IS NOT NULL),
        '{}'
    )                             AS reading_ability_keys
FROM (
    SELECT DISTINCT ON (work_id) *
    FROM   public.labelsets
    ORDER  BY work_id, id DESC
) ls
-- One cover edition per work, chosen deterministically (lowest isbn with a non-null cover_url)
JOIN LATERAL (
    SELECT e.isbn, e.cover_url
    FROM   public.editions e
    WHERE  e.work_id = ls.work_id
      AND  e.cover_url IS NOT NULL
    ORDER  BY e.isbn
    LIMIT  1
) cover_ed ON TRUE
-- Hue keys for this labelset
LEFT JOIN public.labelset_hue_association lha ON lha.labelset_id = ls.id
LEFT JOIN public.hues                     h   ON h.id = lha.hue_id
-- Reading ability keys for this labelset
LEFT JOIN public.labelset_reading_ability_association lra ON lra.labelset_id = ls.id
LEFT JOIN public.reading_abilities                    ra  ON ra.id = lra.reading_ability_id
GROUP BY ls.work_id, ls.id, ls.min_age, ls.max_age, ls.recommend_status,
         cover_ed.isbn, cover_ed.cover_url
    """,
    with_data=True,
)

collection_frequency_view = PGMaterializedView(
    schema="public",
    signature="work_collection_frequency",
    definition="""
SELECT
    e.work_id,
    SUM(ci.copies_total) AS collection_frequency,
    COUNT(DISTINCT c.school_id)
        FILTER (WHERE c.school_id IS NOT NULL) AS school_count
FROM
    public.editions e
JOIN
    public.collection_items ci ON ci.edition_isbn = e.isbn
JOIN
    public.collections c ON c.id = ci.collection_id
GROUP BY
    e.work_id
    """,
    with_data=True,
)


search_view_v1 = PGMaterializedView(
    schema="public",
    signature="search_view_v1",
    definition="""
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
    """,
    with_data=True,
)
