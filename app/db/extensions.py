from alembic_utils.pg_extension import PGExtension

# pg_cron_ex = PGExtension(schema="pg_catalog", signature="pg_cron")
pgvector_ex = PGExtension(schema="public", signature="vector")

# Trigram matching for ranked, typo-tolerant search (e.g. school-name search).
pg_trgm_ex = PGExtension(schema="public", signature="pg_trgm")
