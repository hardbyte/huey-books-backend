# Keep operational school insights separate from durable analytics

Keep the educator API bounded to fixed UTC session-start cohorts and current collection health, using the existing PostgreSQL data and school authorization. Retained events, daily aggregates or DuckDB exports are a separate design decision: copying child-related activity changes deletion and retention obligations, not just query performance. The live dashboard may change as sessions resume or underlying flows/history are deleted; it does not promise durable historical reporting.

The [analytics architecture](../analytics.md) records scaling options and acceptance gates. No new retention policy, deletion-rule changes or analytics infrastructure are approved by this dashboard decision.
