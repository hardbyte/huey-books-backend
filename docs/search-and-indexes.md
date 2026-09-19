# Search, recommendations and index freshness

## Query contracts

`GET /v1/search` reads `search_view_v1` and joins `work_collection_frequency` for popularity. It filters a weighted English tsvector with `websearch_to_tsquery`, ranks with `ts_rank` multiplied by popularity, and highlights with `ts_headline`. It does not use BM25. CMS content has a separate tsvector search implementation. `/v1/editions?query=` uses PostgreSQL full-text matching on edition titles. Work and author lists use substring matching. Library-name matching must use `lower(schools.name)` to match its expression GIN index.

Shared pagination accepts `skip >= 0` and `1 <= limit <= 2000`, default 100; the upper bound preserves existing 2,000-item collection integration requests. All edition-list branches apply pagination in SQL and load only `EditionBrief` fields. Work, author and edition lists have stable ID ordering. The public recommendation endpoint accepts `1 <= limit <= 50`, default 5.

Recommendation eligibility compares the native `recommendstatus` enum directly. Casting the indexed column to text prevents the status predicate using the existing composite index and loses useful selectivity estimates. Hue and reading-ability array indexes do not automatically accelerate their use inside scoring expressions: inspect actual plans.

The recommendation view contains the latest labelset and selected cover edition per work. Eligibility filters are applied when querying it. Ranking weights are library membership 4, campaign/booklist boost 3, reading ability 2 and hue 1, with random ordering within score ties. Candidate hydration loads response fields explicitly.

## Freshness SLA

This is an initial internal operating target, not a contractual customer guarantee: **99% of five-minute observations over 28 days** should meet these committed source-snapshot age limits.

| View | Maximum source-snapshot age | Scheduled refresh |
|---|---:|---|
| `recommendable_editions` | 30 minutes | Every 15 minutes |
| `search_view_v1` | 2 hours | Hourly |
| `work_collection_frequency` | 2 hours | Hourly, with search |

A refresh records its conservative source boundary before the refresh starts and its completion time in `search_index_refreshes`. These timestamps and the materialized-view changes commit in the same transaction. Failed or rolled-back refreshes cannot advance freshness. An absent row is unknown and fails the freshness check. This measures the age of the snapshot, not the exact delay of each source write, nor completeness/relevance of indexed content.

Recommendation label writes also request a best-effort debounced Cloud Task. Its fixed task name can remain unavailable during Cloud Tasks' deduplication window; it is not a trailing-edge debounce or a guarantee that the last write becomes visible after 60 seconds. Scheduled refreshes supply the bounded backstop.

The internal endpoints are:

- `POST /v1/maintenance/refresh-recommendations`: concurrent recommendation refresh and explicit commit.
- `POST /v1/update-search-index`: search and popularity refresh with explicit commit. Popularity refresh is concurrent; search still requires an exclusive lock because its rows are not unique per work.
- `POST /v1/maintenance/check-search-freshness`: read committed timestamps and emit bounded per-index observations plus a completed-check heartbeat.

Refresh callers set a five-second lock timeout and 120-second statement timeout. A busy or failed refresh is retried by the scheduler; stale data remains readable after rollback. Investigate persistent failures rather than increasing timeouts blindly.

The infrastructure repository owns the schedules, log-based metrics, dashboard and alerts. Alert on stale/unknown observations and on an absent completed-check heartbeat for 20 minutes. The dashboard shows observed compliance and sample counts. Missing observations are unknown, not healthy; an absence alert is separate from the observed compliance percentage. New metrics need a full 28-day history before the window is complete.

## Recovery and validation

Check the scheduler's execution result, internal API errors and database locks. Invoke the authenticated refresh endpoint, then check freshness from a new transaction. For a privileged manual SQL refresh, use `SELECT public.refresh_recommendable_editions_function()` or `SELECT public.refresh_search_index()` and commit. Direct `REFRESH MATERIALIZED VIEW` bypasses freshness bookkeeping. Runtime roles execute the restricted SECURITY DEFINER functions; readonly roles cannot refresh views.

After a change, verify snapshot timestamps advance only after successful commit, sample counts resume, alert state recovers and search/recommendation requests remain successful. Keep query plans and timing distributions separate: a single warm-cache plan is not an endpoint latency claim.

## Trace coverage

`READ_TRACE_SAMPLE_RATE` defaults to 1.0 for the selected search/list/recommendation routes, including library collections and booklists. It makes a new server sampling decision even when Cloud Run supplies an unsampled parent. Child spans inherit that decision; existing privacy filtering still applies. `CHAT_TRACE_SAMPLE_RATE` independently controls chat. Health checks remain excluded.

A trace ID in a log proves context propagation, not export. Inspect the log entry's `traceSampled` flag before treating a missing trace as data loss. In the September 2026 review, all 27 unavailable traces were unsampled and all five sampled traces were retrievable. Sampled-but-missing traces require separate exporter/quota/retention investigation. Monitor trace volume and cost when changing rates.

## Follow-up search work

[Issue #779](https://github.com/hardbyte/huey-books-backend/issues/779) tracks coverage/cardinality corrections, native pg_trgm tuning, relevance evaluation and a staging comparison with Cloud SQL's pg_textsearch preview. PlanetScale TIN is a separate provider-dependent experiment. No new search extension is required by the inexpensive query fixes.
