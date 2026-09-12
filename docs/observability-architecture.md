# Observability architecture

Status: proposed target architecture, with the implemented baseline identified
below. Deployment, pricing measurements and rollout evidence belong in the change
record. [Product analytics](analytics-proposal.md) owns business definitions,
privacy, retention and optional retained reporting; this document owns operational
signals, instrumentation and delivery.

## Decision

**OpenTelemetry is the instrumentation contract; Google Cloud Observability is
the managed backend.** Use standard semantic conventions where they exist and a
small `huey.*` namespace for product-specific measurements. Keep platform-native
metrics, structured stdout logs and transactional records where they are the
right tools. OTel-first does not mean exporting every signal twice or turning
every domain event into a span.

Use the existing deployment shape initially. The preferred full OTLP topology is
a per-instance Collector with bounded resources and instance-based CPU allocation,
but it must pass a compute-inclusive cost and delivery trial before adoption.
Do not introduce an always-on collector gateway, scrape fleet or analytics server.
Until that gate passes, keep the bounded trace exporter and existing log-derived
SLIs; do not present periodic SDK metrics from throttled instances as complete.

## Signals and Google services

| Signal | Instrumentation / source | Google destination | Purpose |
| --- | --- | --- | --- |
| Platform health | Cloud Run, Cloud SQL, Tasks and Scheduler native metrics | Cloud Monitoring | Infrastructure availability, capacity, queue health |
| Application latency/counts | OTel counters and histograms; existing log-derived SLIs during migration | Cloud Monitoring (OTLP metrics map to Prometheus time series) | SLOs and bounded operational breakdowns |
| Distributed traces | OTel SDK + FastAPI/HTTP/database instrumentation | Cloud Trace | Per-request causal timing and sampled diagnosis |
| Diagnostic logs | structlog/stdlib JSON with severity and trace correlation | Cloud Logging; Error Reporting where configured | Error investigation independent of trace sampling |
| Browser observations | First-party typed ingestion; server records allowed OTel measurements | Monitoring; limited structured events when needed | Best-effort UX measurements, not unique-user counts |
| Business facts | Domain PostgreSQL records and transactional outbox | Authorized reporting queries; optional approved aggregates | Billing, accepted actions and reproducible reporting |
| Session replay | Existing PostgreSQL execution-step records | Restricted support interface | Sensitive support data; never an OTLP state dump |

Google's Telemetry API accepts OTLP logs, metrics and traces using gRPC or
HTTP/protobuf/JSON. OTLP metrics map to Prometheus time series in Monitoring;
resource/name mappings, temporality and pricing must be tested rather than
assumed identical to custom Monitoring metrics. OTLP is a protocol, not a database
or a retention guarantee. [Google OTLP support](https://docs.cloud.google.com/stackdriver/docs/otlp/overview)

Cloud Logging provides searchable structured records and trace links; Monitoring
provides time series, dashboards, SLOs and alerts; Trace provides distributed span
inspection. Use SQL-capable observability analysis for occasional diagnostics
where already available, not as an implicit durable business warehouse. No
BigQuery export or retained analytics store is required by this design.

## Implemented baseline

- `app/logging.py` installs process-wide FastAPI, HTTPX, SQLAlchemy, psycopg2 and
  asyncpg instrumentation and a batched Google Cloud Trace exporter. Resource
  identity currently uses Google-specific setup; the conventions
  emitted by the pinned instrumentation need a deliberate migration.
- `app/observability/tracing.py` owns privacy-aware span processing and chat
  sampling. `CHAT_TRACE_SAMPLE_RATE` can sample chat despite an unsampled inbound
  Google context; other traffic is parent-based. Version probes/timing ingestion are
  excluded. This is selective head sampling, not universal slow-trace retention.
- `app/observability/propagation.py` prefers valid W3C context, falls back to the
  Cloud Run trace header and injects W3C trace context without baggage.
- Request summaries include route template, generated request ID, status,
  completion/failure and duration. structlog and stdlib logs get Cloud trace/span
  correlation and severity. Unhandled exceptions are logged once in context.
- Privacy filtering occurs before spans enter the background queue. Database
  exceptions retain type/SQLSTATE and safe frames rather than echoed parameters.
  `hide_parameters` and bound SQL are required; SQL commenter is disabled.
- Browser timing uses signed response receipts and bounded per-instance replay
  suppression. Sampling and caps make it advisory, not an availability source.
- The service uses request-based CPU allocation. Background export can pause
  while idle. Queue and RPC limits are in code; abrupt shutdown can lose data.

Keep these guarantees through migration. Source files and tests, not copied
queue sizes or a historic benchmark, define current configuration.

## Semantic convention contract

Pin compatible SDK, instrumentation and exporter versions in the lockfile.
Record the instrumentation library version separately from its semantic-convention
`schema_url` where supported, and assert exported payloads in tests. Stable SDKs do not imply all conventions
or instrumentations are stable. Do not assume schema translation can reconcile
arbitrary upgrades. [OTel telemetry stability](https://opentelemetry.io/docs/specs/otel/telemetry-stability/)

| Concern | Target names / rule |
| --- | --- |
| Resource identity | `service.namespace=huey`, explicit `service.name`, release `service.version`, `deployment.environment.name`; cloud/resource detector attributes where supported |
| HTTP | Stable `http.server.request.duration` histogram in **seconds**; `http.request.method`, `http.response.status_code`, route template `http.route`; no raw paths as labels |
| Database | Stable `db.client.operation.duration` histogram in **seconds**; supported `db.system.name`, `db.operation.name` and low-cardinality query summary; no parameter values |
| Errors | Standard error/exception fields where applicable; sanitize text/stack content before export; outcome counters independent of sampled spans |
| Huey operation grouping | `huey.traffic.class=chat|admin|webhook|background|health|telemetry|other`, bounded `huey.operation` |
| Product observations | Named `huey.*` events/instruments with separate `huey.event.schema_version`; names and semantics in the product catalogue |

HTTP and database domains have mixed stability; the duration instruments above
have stable specifications. Pool metrics and browser conventions require separate
stability review. Some instrumentation exposes `OTEL_SEMCONV_STABILITY_OPT_IN`
(`http`, `database`, or temporary `/dup` modes); verify support in the installed
version. Never leave dual emission enabled after cutover or silently change units
under an existing metric descriptor.
[HTTP metrics](https://opentelemetry.io/docs/specs/semconv/http/http-metrics/),
[database metrics](https://opentelemetry.io/docs/specs/semconv/db/database-metrics/)

Custom names must not masquerade as standard conventions. Proposed browser
durations are `huey.browser.response_to_commit.duration` and
`huey.browser.response_to_next_frame.duration`, in seconds, with bounded operation
and surface attributes. Existing millisecond payloads remain a migration input;
convert once at ingestion and verify distributions. No school, organisation,
ISBN, child, request, session or trace identifier is a metric dimension.

## Instrumentation and correlation

Use standard OTel APIs directly for standard traces/meters; do not wrap their
entire surface. A small product-observation module owns typed application events,
eligibility, deduplication, privacy and sampling. Feature code must not know about
Google credentials, sinks, JWTs or replay caches. It can record a cover presentation
or a validated import outcome without choosing a transport.

Create a server span per request and appropriate client/database spans. Verify
SQLAlchemy and driver instrumentation do not double-count the same SQL operation;
connection checkout and query execution are separate intervals. Explicit business
spans should identify expensive operations, not mirror every Python function.

Prefer W3C `traceparent`/`tracestate` across our services. During migration, accept
Google's context where needed for Cloud Run native request-log correlation. Define
and test deterministic precedence when both are present; never combine IDs from
different parents. Propagate only to allowlisted destinations. Do not propagate
arbitrary baggage or use trace IDs/context for authentication, tenancy or identity.
Record a new request ID independently. A valid unsampled trace context can still
correlate logs even if no span will be available in Trace.

For queued work, preserve context only in trusted task metadata. Use a producer/
consumer relationship or a span link appropriate to the queue's supported
conventions; retries are distinct processing attempts, not duplicate business
facts. Instrument queue delay separately from handler duration and delivery age.

## Export topology and Cloud Run

```text
FastAPI instrumentation ──→ OTel SDK ──→ bounded export
                                             │
                        baseline: Google Trace exporter
                        target: local OTLP Collector → Google Telemetry API

structlog/stdlib ──→ redacted structured stdout ──→ Cloud Logging
Cloud Run / SQL / Tasks native metrics ───────────→ Cloud Monitoring
Browser ──→ first-party validated observations ──→ server-side instruments/events
```

Keep structured stdout as the sole log delivery path initially. It is supported
and recommended on Cloud Run, integrates native capture, and avoids another
background exporter. Preserve severity and `logging.googleapis.com/trace`,
`logging.googleapis.com/spanId`, `logging.googleapis.com/trace_sampled`
correlation fields. This is an output mapping, not the
product's event schema. Do not enable OTLP log export alongside stdout for the
same records. [Google instrumentation guidance](https://docs.cloud.google.com/stackdriver/docs/instrumentation/choose-approach)

The preferred Collector topology centralizes ADC authentication/refresh, resource
detection, filtering, batch export and memory limits. Run it as a sidecar with
instance-based billing; restrict its receiver to the application instance.
Pin a supported Collector image/configuration, allowlist exported attributes and
grant only the documented writer/quota permissions. Google currently requires a
sufficiently recent Collector for OTLP metrics; verify the selected version.
[Cloud Run Collector guide](https://docs.cloud.google.com/stackdriver/docs/instrumentation/opentelemetry-collector-cloud-run)

Direct OTLP gRPC with ADC is a valid lower-component-count alternative, but needs
token refresh and signal-specific configuration inside the application. It does
not solve idle CPU starvation. A sidecar without the CPU change does not solve it
either. Trial both against measured low-traffic delivery and total compute cost;
retain the baseline if the target's benefit does not justify its cost.

Do not network-flush each request or database checkout. Bound SDK/Collector queues,
export deadlines, retries and shutdown time. Cloud Run sends termination signals
to containers and has a short shutdown window; a sidecar is not a durable queue
or a guarantee of final delivery. Test graceful termination, cold start, sustained
idle, exporter outage and token refresh. [Cloud Run lifecycle](https://docs.cloud.google.com/run/docs/container-contract)

For metrics, test cumulative/delta temporality, sparse observations, process resets,
resource uniqueness and multiple instances. Prefer cumulative counters with
verified reset handling; inspect the exported Monitoring representation before
choosing queries. A short-lived instance can lose its final interval. Keep native
platform signals and log-derived SLIs until the replacement demonstrates sufficient
coverage; product facts must never depend on those last samples arriving.

## SLOs, sampling and economics

Maintain separate chat, admin, webhook and background SLOs. Health probes,
scanner/unmatched requests and telemetry ingestion must not dominate denominators.
Define eligible statuses, time boundaries and failure semantics explicitly. Client
errors may need their own usability/authentication metric rather than counting as
service availability failures. A completed webhook request is not proof of a paid
invoice being applied; a successful job handler is not proof of queue freshness.

Use histograms with buckets around actual latency objectives; aggregate buckets
across instances/windows, not averages of percentiles. Keep browser timing separate
from server SLIs and show observation counts. Native HTTP timing, application
response completion, background work and browser rendering measure different
intervals. Confirm semantic parity before replacing existing log-derived SLOs.

Metrics and essential exception logs must not inherit trace sampling. Head
sampling cannot guarantee retaining errors or slow requests discovered later.
Keep the current configurable chat coverage while volumes permit; change sample
rates with measured span budgets. Tail sampling requires trace affinity, buffering
and a reliable collector topology; do not add it merely to claim all slow traces
are retained. [OTel sampling](https://opentelemetry.io/docs/concepts/sampling/)

Track log bytes, span count, active series and exported metric samples as well as
application/sidecar CPU and memory. Histogram buckets, dimensions, resource churn
and export interval affect metric cost. OTLP metrics use Prometheus pricing;
log-derived metrics are not free substitutes. Avoid duplicating SDK HTTP metrics,
native request metrics and log-derived metrics without a distinct question or a
bounded migration period. Current prices/allowances belong in a measured cost
worksheet, not hardcoded architectural promises.
[Google observability pricing](https://cloud.google.com/products/observability/pricing)

## Browser and sensitive data

Do not ship Google credentials or expose an unrestricted OTLP receiver to browsers.
Use first-party ingestion with allowlisted typed payloads, receipt validation,
request limits and privacy filtering. Retain browser measurements as untrusted,
best-effort observations. OTel JavaScript browser instrumentation remains
experimental; backend OTel adoption is not a reason to add automatic DOM capture,
fetch-body capture, session replay or an SDK to every page.
[OTel JavaScript status](https://opentelemetry.io/docs/languages/js/)

General OTel event conventions are still developing. Model product events as
named structured records with application-owned schemas, not arbitrary span
events requiring a sampled parent. Counters can be derived from validated
observations without retaining every raw record. OTel does not supply business
durability or consent/retention policy. [Event conventions](https://opentelemetry.io/docs/specs/semconv/general/events/)

Filter secrets and child content before buffering or export. No SQL parameters,
request bodies, token-bearing URLs, cookies, credentials or conversation state.
Collector redaction is defence in depth, not a replacement for source filtering.
Operational query templates must be parameterized; literals already embedded in
SQL may contain sensitive content. Disable SQL commenter unless a separately
reviewed need outweighs cache fragmentation and metadata leakage.

Application middleware cannot redact Cloud Run's native request logs before
capture. Existing token-in-path chat routes therefore remain a known exposure
until their callers and routes are removed together. Do not claim removal here:
it requires a caller audit, migration of first-party clients and removal tests.
Historical logs and their retention need a separate access/deletion review.

## Implementation and removal gates

1. Establish payload fixtures for the pinned OTel stack, metric definitions and
   allowed attributes. Verify standard units, resource identity, propagation and
   correlation with a real HTTP request and PostgreSQL query.
2. Add the shared observation module and migrate existing timing plus cover
   measurements. Do not add a separate cover receipt/cache/ingestion subsystem.
   Test hidden/offscreen/error states, replay, rejection and telemetry failure.
3. Trial OTLP export in staging with the Collector/CPU cost gate above. Prove low
   traffic, restart, concurrent instances and failure behaviours. No migration may
   recreate synchronous trace export on the request path.
4. Compare dashboards/SLIs during a bounded overlap, then remove superseded
   exporters, duplicate instruments, metric definitions, old ingestion routes,
   client helpers, configuration, dependencies and tests. Do not retain deprecated
   forwarding layers. Historical series may remain under provider retention, but
   must no longer receive duplicate new data.
5. Recheck production correlation, latency, signal coverage, privacy and costs.
   Rollback uses a known release/configuration, not permanent duplicate pipelines.

Existing regression entry points: `app/tests/unit/test_observability.py`,
`app/tests/unit/test_browser_timing.py`, and the PostgreSQL `test_trace_export.py`
integration suite. Extend their behavioural assertions instead of treating a green
exporter startup as proof of delivery. Trace sampling rates, SLO budgets and export
limits belong in code/Terraform; changes require review and a measured rollout.
