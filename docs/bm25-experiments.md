# BM25 experiment setup

Cloud SQL's [native BM25 preview](https://cloud.google.com/blog/products/databases/native-bm25-search-in-alloydb-and-cloud-sql) uses `pg_textsearch`. Both Huey databases run PostgreSQL 18. Enabling the extension prepares experiments; application search continues to use the queries described in [Search and indexes](search-and-indexes.md).

## Deployment order

1. Apply the infrastructure repository's `cloudsql.enable_pg_textsearch=on` flag to development, then production. Cloud SQL reports that this flag **requires an instance restart**. Preserve existing flags, and allow the instance to become healthy before migrating databases. See the infrastructure repository's `docs/cloudsql-bm25.md` for the import and rollout procedure.
2. Deploy the backend through its normal pipeline. Alembic revision `c13ed852fa90` creates `public.pg_textsearch` in each migrated database. Its definition is registered in `app/db/extensions.py` and `alembic/env.py` for future schema comparisons. A missing preload or unavailable extension fails deployment instead of silently skipping enablement.
3. Verify the installed version and index access method:

   ```sql
   SELECT extname, extversion FROM pg_extension WHERE extname = 'pg_textsearch';
   SELECT amname FROM pg_am WHERE amname = 'bm25';
   SHOW shared_preload_libraries;
   ```

Cloud SQL offered version **1.3.1** when this change was prepared. The migration installs the provider's default version; existing installations are not upgraded by application startup. Review extension upgrades separately and record the actual server version in every benchmark.

## Local development and CI

`docker/postgres.Dockerfile` builds the checksum-pinned upstream 1.3.1 release against PostgreSQL 18. Compose and the migration workflow use this image with the extension preloaded. Rebuild the database image with `docker compose build db`, then recreate the database container with `docker compose up -d db` and apply migrations. Keep existing volumes. A standalone PostgreSQL server also needs the extension files installed and the preload configured before applying migrations.

The integration test creates an isolated probe, builds a BM25 index and checks ranking. Migration CI tests a fresh installation and a ten-revision downgrade/re-upgrade.

## Bounded production probes

Use a privileged experiment connection and an explicit transaction for disposable probes. Keep statement and lock timeouts bounded. Start with a small synthetic table, then use a separately reviewed dataset/index for catalogue experiments:

```sql
BEGIN;
SET LOCAL statement_timeout = '10s';
SET LOCAL lock_timeout = '2s';
CREATE TEMP TABLE bm25_probe (id integer PRIMARY KEY, content text);
INSERT INTO bm25_probe VALUES
  (1, 'dragon books and dragon stories'),
  (2, 'gardening tips'),
  (3, 'books about dragon adventures');
CREATE INDEX ON bm25_probe USING bm25(content) WITH (text_config='english');
SELECT id, content <@> 'dragon' AS score
FROM bm25_probe ORDER BY content <@> 'dragon' LIMIT 2;
ROLLBACK;
```

The distance operator sorts ascending: more negative scores rank higher. See [Cloud SQL's extension guide](https://docs.cloud.google.com/sql/docs/postgres/pg-textsearch). Evaluate tenant/library filters, authorless works, series cardinality, popularity weighting, typo/substring behavior and relevance before routing user searches to it. In particular, test restrictive filters and result counts against the installed version: upstream [1.3.1 documentation](https://github.com/timescale/pg_textsearch/blob/v1.3.1/README.md) describes limitations when filtering top-k results. Track experiments in [issue #779](https://github.com/hardbyte/huey-books-backend/issues/779).

## Rollback

An application rollback can leave the unused extension installed. The migration's downgrade uses `DROP EXTENSION ... RESTRICT`: it refuses to remove an extension that experiment indexes depend on. Inventory and explicitly remove experiment objects before downgrading. Do not use `CASCADE`. Removing the Cloud SQL flag is a separate infrastructure change requiring another restart, and must follow removal of extension dependencies from every database on the instance.
