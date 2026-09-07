# Low-cost school analytics

Proposal for review, 7 September 2026. No infrastructure changes are included.

## Recommendation

Keep the first school Insights page on bounded, school-scoped PostgreSQL queries. Measure its cost before introducing a pipeline. If dashboard reads become material, move engagement metrics to **daily PostgreSQL aggregate tables**, retaining the same educator API. A **scheduled DuckDB job over private Parquet** is a good later option for longer-history analysis and repeated exploratory queries, not a prerequisite for this page. Avoid an always-on analytics service or warehouse.

This is an architectural recommendation, not a measured cost comparison. The existing small Cloud SQL instance also serves student chats: protecting its latency matters more than reducing dashboard latency alone.

| Approach | Benefit | Cost and limitation | When to use |
| --- | --- | --- | --- |
| Indexed, bounded live PostgreSQL | No new service or duplicated data; current collection coverage | Each dashboard read consumes database CPU/I/O; JSON extraction and history joins need measurement | Initial release with constrained date ranges |
| Daily PostgreSQL aggregate tables | Cheap indexed API reads; one scheduled recomputation instead of repeated scans | Computation still runs on the primary; delayed freshness | First scaling step for stable school metrics |
| Cloud Run job + DuckDB + private Parquet | Repeated analytical scans happen outside PostgreSQL; reproducible offline analysis | Extraction still costs database I/O, plus storage, compute and pipeline maintenance | Longer history, multiple analyses reusing the same export |

Prefer partition replacement/upserts in aggregate tables over assuming materialized views are incremental: PostgreSQL refresh replaces the view contents; concurrent refresh permits reads but requires a suitable unique index. [PostgreSQL documentation](https://www.postgresql.org/docs/current/sql-refreshmaterializedview.html)

## Define the numbers before moving them

The implementation sources are [school insight queries](../app/repositories/school_insights.py) and [session/history models](../app/models/cms.py). School attribution is a typed session foreign key, resolved at creation; legacy rows are inferred only from matching school links. Recommendation milestones and validated feedback come from `conversation_history`; hue selections still use current session state. Consequently:

- Count **sessions**, not unique students. Anonymous sessions do not establish identity or attendance.
- Distinguish “reached recommendations”, “submitted feedback” and any actual journey-completion event. A feedback milestone does not prove the child browsed every book.
- “Looks good” is expressed interest, not a loan, completed read or learning outcome. “Already read” is self-reported.
- Hue state is mutable, and latest-submission feedback figures can change after a session resumes. New submissions retain validated choices in history; legacy choices without recorded offered books are not reconstructed or treated as zero.
- Collection coverage is a current snapshot, not historical availability. Distinct works, catalogue items and copies are different units. An unchecked label is not automatically a missing label.
- Keep latency and system errors on the staff dashboard. Regional usage should use explicitly defined school location, not infer children's locations from IP addresses.

For new metrics, add small server-recorded, versioned events such as `recommendations_presented`, `feedback_submitted` and `recommendations_finished`, with event ID, session key, trusted school attribution, work ID where needed, and server timestamp. Avoid message text and tokens. Record feedback revisions explicitly and define whether a metric counts actions or final per-work choices. Do not present guessed historical backfills as observed events.

## A minimal scheduled design

1. One daily run outside busy school hours, processing all schools in bounded batches. Store UTC dates initially and document the boundary; changing to school-local days needs an explicit timezone and rebucketing policy. Weekly charts can sum daily counts, but distinct-session metrics must retain a consistent cohort basis rather than sum overlapping distinct counts.
2. Maintain engagement rows keyed by `(school_id, cohort_date, metric_version)` and a separate dated collection snapshot. Include `computed_at`, source cutoff and run ID. Store counts, not rounded percentages; calculate rates from the matching numerator and denominator.
3. Recompute recent cohorts plus older cohorts affected by changed sessions. A `started_at`-only watermark misses late feedback. Verify that `last_activity_at`/revision advances on every relevant write before using it; otherwise introduce a reliable change marker. Reconcile older retained cohorts weekly and propagate deletions/corrections. A fixed seven-day lookback alone is not a correctness guarantee.
4. Build replacement results, validate them, then publish the affected rows and checkpoint atomically. Prevent overlapping runs and replace counts rather than incrementing them on retries. Cloud Scheduler delivery is at least once, so duplicate execution must be safe. [Cloud Scheduler documentation](https://docs.cloud.google.com/scheduler/docs/overview)
5. Serve results through the existing authenticated school API, including backend school authorization and effective “View as” permissions. Never expose cross-school backing tables or object URLs to educators. Return freshness and explicit stale/unavailable states; keep the last successful snapshot after a job failure, not a fabricated zero or an unbounded fallback query.

Preserve the repository/service boundary: the service owns authorization, suppression and response definitions; the repository can switch from live queries to snapshots. Keep metric-version and parity fixtures independent of SQL engine. This avoids rewriting the UI if the storage choice changes.

## Where DuckDB fits

Cloud Scheduler can invoke a Cloud Run job, avoiding a resident analytics server. Use a single task initially, bounded memory/runtime, and the same region as the database and bucket. [Cloud Run scheduling documentation](https://docs.cloud.google.com/run/docs/execute/jobs-on-schedule)

Export only required typed fields through a restricted read-only database role, using bounded time/key ranges and a consistent cutoff. Do not export entire JSON state, conversation text, session tokens, email addresses or user profiles. Close the database connection before expensive DuckDB analysis. Read-only attachment is supported by DuckDB, but prefer an explicit extraction query whose database plan and transferred bytes can be measured; remote scanning is not free database offloading. [DuckDB PostgreSQL extension](https://duckdb.org/docs/current/core_extensions/postgres/overview)

Write compact date-partitioned Parquet to a private bucket; avoid tiny per-school files. DuckDB can read/write Parquet and push column selection and filters into its reader, allowing subsequent analyses to reuse the export without rereading PostgreSQL. [DuckDB Parquet documentation](https://duckdb.org/docs/current/data/parquet/overview)

Produce the same aggregate rows as the PostgreSQL implementation and publish only those to the serving database. A local DuckDB file is a disposable job artifact, not a shared API datastore. Export generation, manifest publication, checkpoint advancement and cleanup need retry-safe ordering; partial files must not become the current snapshot. Deleted records need tombstones or partition rebuilding, not just append-only exports.

Start with daily extraction if educator freshness requires it and weekly exploratory analysis over those files. Weekly extraction is reasonable only if week-old figures are acceptable; it reduces invocation frequency but not necessarily total rows scanned. Do not promise free operation: estimate measured task duration × allocated CPU/memory, including job minimum billing, plus Scheduler, storage/operations, logs, networking and primary-database load. Cloud Run pricing is regional and free allowances are shared. [Cloud Run pricing](https://cloud.google.com/run/pricing)

## Privacy and acceptance gates

- Keep educator output aggregate-only, without session drill-down, student rankings or raw exports. Apply small-group suppression consistently to counts, rates, totals and breakdowns; fixed reporting windows reduce differencing attacks. Five sessions are not necessarily five people, so a session threshold alone is not an anonymity guarantee.
- Use separate least-privilege extract and aggregate-write permissions. Any retained session linkage is pseudonymous, not anonymous. Keep it private and only as long as correction/reconciliation requires it.
- Choose a retention period before enabling exports; apply lifecycle deletion and verify deletion requests cover exports, aggregate rebuilds and retained versions. Cloud Storage supports lifecycle rules, but these do not replace application-level deletion propagation. [Cloud Storage lifecycle documentation](https://docs.cloud.google.com/storage/docs/lifecycle)
- Benchmark the largest school and longest supported range: query plans, buffers, database CPU/I/O, connection time, API latency and concurrent student-chat latency. Use bounded staging tests before any production load experiment.
- Shadow one daily job before switching serving: compare empty schools, duplicate runs, resumed old sessions, feedback revisions, deleted sessions and date boundaries. Verify no cross-school access or suppressed-value recovery via alternate API filters.
- Adopt aggregate tables when repeated live reads materially affect the database or miss the agreed dashboard latency target. Adopt DuckDB only if a measured prototype reduces primary load or enables useful analysis at acceptable total operating cost. Neither decision requires introducing paid always-on BI hosting.
