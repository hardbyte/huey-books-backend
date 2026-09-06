# Trace export and log correlation

## Acceptance criteria

- Instrumented database checkout must not wait for trace network export. Keep pool health checks.
- Bound the export queue and individual export RPCs. Drain queued spans on graceful application
  shutdown without blocking the asyncio event loop. Do not force-flush each request.
- Correlate structured and standard-library logs with the current Cloud Trace trace/span,
  including valid unsampled context. Emit Cloud Logging severity.
- Emit a request summary with a generated request ID, method, route template, status and duration;
  return the request ID in normal response headers. Do not include session tokens, raw URLs,
  query strings, request bodies or children's answers in these summaries.
- Verify locally, review the PR against its main base, pass CI, merge and check production
  request latency and correlated application logs. No KPI/CMS changes belong in this release.

## Operational behaviour

The batch processor holds at most 1,024 spans, exports at most 512 per batch, and wakes every
200 ms while CPU is available. Cloud Trace writes use a two-second RPC deadline without
automatic retries. Telemetry is best-effort: exporter failures or a full queue can drop spans.
Application requests do not wait for the export. Graceful lifespan shutdown drains the
processor from a worker thread; abrupt termination can still lose queued telemetry.

Cloud Run's existing request-based CPU allocation and billing remain unchanged. Export can
pause while an instance is idle, and resume on the next request or during graceful shutdown.
Logs still go to stdout/stderr immediately. This trade-off avoids changing the project's
hosting costs; immediate idle trace delivery would require separately reviewing CPU allocation.

The timing regression probe uses real pooled PostgreSQL connections and instrumented health
checks with a deliberately slow exporter. Run it through the integration suite:
`bash scripts/integration-tests.sh -k trace_export`.

Production checks should confirm shorter CONNECT spans, successful chat interactions,
`HTTP request completed` log entries linked to the same trace as Cloud Run request logs,
and no sustained trace-export failures. Existing Cloud Run request logs and pre-existing
application logs are not redacted by the request-summary middleware.
