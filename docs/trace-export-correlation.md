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
- Unhandled exceptions are logged once, inside the request ID and trace context. The generic
  error response and Uvicorn must not emit duplicate tracebacks after that context unwinds.
- Successful health probes and optional Uvicorn access logs are disabled at the default
  INFO level. Request summaries replace application-level raw URL access logs.

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

## Sampling and privacy

Chat server spans use `CHAT_TRACE_SAMPLE_RATE` (0–1, default 1), including when Cloud Run
supplies an unsampled remote parent. Descendant spans follow that decision. Health and browser
telemetry endpoints are not sampled; other traffic retains parent-based sampling. Capturing
complete chat traces makes slow responses diagnosable without a synchronous exporter or tail
collector. This does not guarantee every slow admin/background trace is retained. Revisit the
chat sample rate as traffic grows; unsampled request summaries still provide durations.

Sensitive-request context wraps the OpenTelemetry middleware. Logs are redacted after exception
formatting in both console and JSON modes. A span processor redacts attributes, events and
status before enqueueing spans: the background exporter cannot access request ContextVars.
Neither request bodies nor child answers should be logged; debug logging is not an exemption.

New chat clients carry `X-Chat-Session` on `/v1/chat/session`, `/interact`, `/end`, `/history`
and `/state`, rather than putting credentials in URLs. Existing `/v1/chat/sessions/{token}`
routes remain compatible but deprecated. CSRF checks still apply to mutations. Application
logs and exported traces redact legacy paths, but Cloud Run generates native request logs
before application middleware: legacy callers can still put tokens there. Historical logs
are unchanged. Finish migrating clients before removing old routes or shortening retention.

## Request SLO signals

`HTTP request completed` includes `traffic_class` (`chat`, `admin`, `webhook`, `background`,
`health`, `telemetry`, `other`), the wire `status_code`, `failed`, `response_complete`, and
`duration_ms`. Availability uses `failed=false`, not just status below 500: a stream can fail
after headers were sent. Duration ends at the last response body, excluding later background
tasks; an exception after the response still sets `failed=true`. Unmatched scanner requests,
health probes and client telemetry are separate from the four product SLOs. Classification is
an operational route grouping, not proof of who called a route. Terraform in `hardbyte-iac`
owns metric descriptors, initial latency budgets and alert policies.

## Browser timing

Successful chat start/interaction responses expose `X-Request-ID` and an
`X-Response-Timing-Token`. The latter is a five-minute signed receipt, audience-bound to
browser timing and bound to the response request ID and operation; it grants no chat access.
Clients POST it as a header to `/v1/chat/telemetry` with `operation` (`start` or `interact`),
`request_duration_ms`, `response_to_commit_ms`, and `response_to_next_frame_ms`. Only finite
values between 0 and 60,000 ms are accepted; unknown fields and bodies above 1 KiB are rejected.
The application logs accepted reports as `Browser response timing`, including
`response_request_id` for linking back to the original summary, never a session or school ID.

Clients sample 10% of eligible responses, at most ten reports per page. Receipts are deduplicated
with a hard-bounded in-memory cache per instance. A receipt can be replayed across instances or
after a restart: these are advisory client diagnostics, not billing, trusted usage counts or
availability SLOs. The next animation-frame callback is not a browser paint measurement.
All telemetry is best-effort and must not delay or fail the chat workflow.

Regression coverage is in `app/tests/unit/test_observability.py` and
`app/tests/unit/test_browser_timing.py`. Production checks should verify correlated errors,
token-free current-client request URLs, completed chat traces, no sustained exporter failures,
and latency distributions split by traffic class rather than dominated by health checks.
