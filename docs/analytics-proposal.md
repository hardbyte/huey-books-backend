# Application metrics and analytics

Status: proposal. Covers the student site, educator/admin application, API, jobs,
billing and school/organisation reporting. This document does not authorize new
retention, exports, infrastructure or changes to existing deletion behaviour.
The [operational Insights decision](adr/0001-operational-insights-boundary.md)
remains in force until explicitly superseded.

## Recommendation

Use OpenTelemetry APIs and semantic conventions as the instrumentation contract,
with Google Cloud as the managed observability backend. The
[observability architecture](observability-architecture.md) defines signal mapping,
export, correlation, sampling and migration. Product definitions and retention
decisions live here; neither document implies that proposed collection is deployed.

- **Operational metrics:** OpenTelemetry counters/histograms and platform-native
  measurements in Cloud Monitoring; correlated logs and traces for diagnosis.
  Existing log-derived SLIs remain until a measured replacement passes parity.
- **Product observations:** first-party, privacy-minimised browser/server events
  with explicit measurement semantics. Use a typed observation module that maps
  to OTel instruments and, when needed, structured events. Do not write each view
  to PostgreSQL or a trace, or make a new ingestion endpoint for each feature.
- **Business facts:** authoritative transactional records for payments, imports,
  permissions and accepted feedback. Retain existing domain records and outbox
  guarantees; do not reconstruct these facts from sampled logs or browser events.
- **Reporting:** retain bounded PostgreSQL queries for current school Insights.
  Add daily aggregates only when measurements justify them. Use a scheduled
  DuckDB job over private Parquet when multiple analyses can reuse a restricted
  extract without repeatedly scanning the primary database.

Do not introduce an always-on BI server, a message broker, a warehouse or a
third-party child-tracking SDK for this work. The next investment is consistent
definitions, instrumentation and trustworthy reports, not a new platform.

## Existing foundations and gaps

These are implementation entry points, not a claim that every endpoint is sound:

| Area | Source | Relevant constraint |
| --- | --- | --- |
| Request latency/errors | `app/middleware/request_logging.py`, `app/logging.py` | Route templates, request/trace correlation and traffic classes; do not use raw token-bearing paths |
| Browser timing | `app/schemas/browser_timing.py`, `app/services/browser_timing.py`, receipt middleware; student `useResponseTiming` | Sampled observations, signed receipts, bounded per-instance deduplication; next-frame timing is not proof of painted pixels |
| Educator Insights | `app/repositories/school_insights.py`, `app/services/school_insights.py`, `app/schemas/school_insights.py` | Fixed UTC start cohorts, latest available outcomes, current collection snapshot, suppression |
| Flow analytics | `app/services/analytics.py`, `app/api/analytics.py` | Mixed real queries and simulated values; review each exposed field before using it |
| Business history/delivery | Domain tables, `app/models/event.py`, `app/models/event_outbox.py` | Editorial/audit events and notification delivery are not a generic page-view store |
| Monitoring declarations | `hardbyte-iac` monitoring Terraform | Existing traffic-class SLOs, browser distributions and outbox monitoring |

Legacy `AnalyticsService` still contains simulated response/error figures,
hash-derived content engagement and export progress. The implementation audit
must map those methods to routes, frontend consumers and tests before their
removal is shipped. This design does not claim that runtime cleanup is complete.
Retire an unsupported feature by removing its implementation, route, client
controls/types and fabricated-success tests together; do not leave deprecated
stubs. For a retained metric, implement its real source or an explicit unavailable
state rather than a fabricated zero. Inter-history timestamp differences can
include a child's reading/thinking time and must not be labelled API latency.

## Metric contracts before instrumentation

Keep a versioned catalogue beside the schemas/tests. Every metric needs an owner,
decision it supports, unit, producer, eligible population, timestamp/cohort basis,
deduplication key, sampling policy, permitted dimensions, retention class and
availability states. Ratios must name both numerator and denominator.

| Question | Definition / source | Important exclusions or caveats |
| --- | --- | --- |
| Is Huey responsive? | Server request duration and failures by chat/admin/webhook/background operation; browser request, response-to-commit and response-to-next-frame distributions separately | Do not subtract aggregate percentiles or call node dwell time latency; report sample counts |
| Are recommendations useful? | Sessions reaching recommendations; validated feedback actions and latest per-ISBN choice reported separately | Served is not viewed; liked is not borrowed/read; completion needs an explicit milestone |
| Where do school signups stall? | Distinct organisation/library signup cohorts reaching account creation, verified staff access, catalogue import and first student recommendations | Define order and conversion window, handle late outcomes, exclude fixtures; invoice payment is a separate commercial milestone |
| Which regions use Huey? | Distinct active library sites and recommendation sessions attributed to trusted site location | Never geolocate children from IP; missing attribution is unknown, not guessed |
| Are imports and reviews working? | Completed/failed imports, accepted/rejected rows, duration; submitted versus published reviews | Count retries once by operation ID; preserve human-review provenance |
| How often do covers differ? | Measured cover presentations partitioned into exact, alternative and placeholder; alternative disclosure views | Same eligibility/sampling/deduplication for numerator and denominator; API selection is not a browser impression |
| Are we financially sustainable? | Actual allocated operating cost divided by defined monthly active sites and organisations; show both | A branch is not a subscription; Stripe's paid state is authoritative, not a webhook HTTP 200 |

Use `organisation`, `library site` and `collection` consistently. Attribute usage
to the site where it occurred; aggregate organisation reporting from authorized
sites. Retain event-time organisation/region attribution for historical reports
or explicitly label a report as current-membership attribution. Moving a site
must not silently rewrite the meaning of a historical chart. School remains a
compatibility term where required by the existing interface.

Sessions are not unique children. Use no cross-site student identifier or browser
fingerprint. Define a monthly active site as one with at least one non-test
student session reaching recommendations in that UTC calendar month. Report
active organisations as distinct organisations containing those sites; report
unaffiliated sites separately. Keep administrative-only activity as a different
metric. These definitions are proposed, not a reinterpretation of existing charts.

## One instrumentation module, different reliability classes

Feature code should express a typed fact or observation, not construct JWTs,
choose log sinks or manage retry caches. Put validation, receipt verification,
privacy filtering, sampling and bounded delivery inside the module. Use OTel
directly for standard operational instrumentation; do not wrap its entire API.
The small product-observation interface earns its place by owning application
semantics and privacy. Business transaction/outbox ownership stays in domain
services, not in a best-effort telemetry emitter.

### Server facts

Derive actor/site attribution from authenticated domain context, never a client
supplied school ID. Use an event ID and server UTC occurrence timestamp. Record
domain facts with the business transaction when durable reconstruction is
required. Reuse the outbox only for reliable delivery of those facts, not for
high-volume browser views. Consumer retries must deduplicate by stable event ID.
An emitted log is diagnostic, not proof that the transaction committed.

### Browser observations

Use one first-party ingestion interface with a discriminated union of allowed
event types and bounded fields, not arbitrary event names/property dictionaries.
Keep schema version, server `received_at`, source surface and sampling probability.
Client durations are bounded measurements; the server timestamp is authoritative.
Do not accept the browser's clock as a trustworthy event date.

Reuse the timing receipt pattern through a shared implementation rather than
adding a cover-specific authentication/cache protocol. A receipt must be scoped
to the allowed observation or response, audience and expiry; telemetry receipts
must never authorize application reads/writes. Derive permitted event kind and
dimensions from server-issued claims where possible. Reject unknown fields,
oversized bodies, invalid signatures and excessive batches before logging.

Receipts prevent arbitrary forgery, not dishonest view claims or abuse by someone
able to obtain many legitimate responses. Bound issuance, ingestion rate, batch
size and per-page reports. Any IP-based abuse controls must stay separate from
analytics attribution and must not add IP addresses to events.

Use best-effort delivery, bounded queues and at most a bounded retry; analytics
failure must never block reading. Browser events carry no auth/session cookies,
URL/referrer, names, email, free text, ISBN or school ID unless an explicitly
reviewed metric requires one. Do not persist a browser identity to deduplicate.
Use ephemeral presentation/event IDs, with a bounded server replay cache.
Per-instance deduplication is approximate across restarts/replicas; if durable
event analysis is later enabled, deduplicate again there. Never claim exactly-once
delivery from an in-memory cache.

### Cover selection as the first product observation

Keep the selected edition's ISBN and metadata. Prefer its cover; otherwise choose
the newest known edition of the same work with a nonblank cover URL, with unknown
dates last and ISBN as a stable tie-breaker. Return alternative cover provenance
separately so older clients cannot silently display borrowed artwork as exact.
No cover yields a placeholder. Broken-image handling happens in the browser,
not a remote HTTP probe in the recommendation request.

Define a measured presentation as at least 50% of the displayed cover visible for
one continuous second while the document is visible, after image load (or after
placeholder rendering). Use that same rule for every cover kind. Measure the
alternative-cover disclosure separately when its text is also visible for one
continuous second. This is a visibility proxy, not proof of attention or reading;
overlay occlusion is not reliably captured by ordinary intersection observation.

Emit the application-defined event `huey.book.cover.presented` with
`huey.event.schema_version=1`, `huey.book.cover.kind=exact|alternative|placeholder`
and allowlisted `huey.ui.surface`. These are Huey conventions, not standard OTel
attributes. Record the `huey.book.cover.presentations` counter from accepted
observations. The shared cover UI owns measurement and deduplicates
rerenders for the same presentation. Carousel navigation to a new presentation
can count again; repeated observer callbacks cannot. Emit
`huey.book.cover.disclosure_viewed` / counter `huey.book.cover.disclosure.views`
at most once for a measured alternative presentation whose notice qualifies.
Keep failed image loads as a separate bounded diagnostic. If an alternative
fails, show a placeholder and do not count a disclosure impression.

Start with the same sampling policy for all cover kinds. The alternative fraction
is `alternative presentations / all measured presentations` for matching surfaces
and presentation cohorts. The disclosure rate is `qualifying disclosures /
measured alternative presentations` for that same cohort, not a ratio of independently
received events across a clock boundary. Show observed counts and sampling coverage.
Sample once per presentation and share that decision with the disclosure event;
the observation module must preserve their relationship without retaining a user
identity. If only alternative
notices are initially instrumented, show their count only: it has no valid
percentage denominator and is not a count of people. Ad blockers, offline clients,
expiry and deduplication limits mean even unsampled telemetry is incomplete.

## Storage and reporting architecture

```text
Requests/jobs ───────────────→ OTel signals → Google observability
Browser observations ───────→ typed ingestion → OTel metrics / allowed events
Committed business facts ───→ PostgreSQL / existing reliable outbox
                                       │
                         optional bounded daily extraction
                                       ↓
                    private Parquet → scheduled DuckDB job
                                       ↓
                          versioned reporting aggregates
                                       ↓
                      authorized staff / educator reports
```

Operational counters and latency distributions belong in Monitoring. Use bounded
dimensions such as operation, traffic class, outcome and surface. Do not label
time series by ISBN, school, organisation, request ID or session ID. Keep request
and trace IDs only in diagnostic records with restricted access. Histograms must
merge bucket counts, not average daily p95s.

Use counters for aggregate questions; retain structured observations only where
event-level investigation is needed and approved. Log-derived metrics are an
initial delivery option while OTel metric export is validated on Cloud Run, not
an instruction to log every click indefinitely. They count entries or distributions and
are not retroactively populated; create metric definitions before relying on
them. Missing telemetry must not automatically become zero. A counted log per
observation and a batched log carrying `count=N` need different aggregation: an
entry counter on the latter counts batches, not impressions.
[Cloud Logging metrics](https://docs.cloud.google.com/logging/docs/logs-based-metrics)

Keep educator output behind the existing authenticated reporting interface. The
service owns definitions and suppression, repositories own SQL, and the API owns
request validation/auth dependencies. Apply effective View-as permissions and
organisation/site authorization on every read. No raw bucket access, arbitrary
SQL, session drill-down or cross-school breakdowns for educators.

Current bounded live queries remain appropriate for current collection health
and operational Insights. Durable history is optional and needs its own approval:

1. Publish daily PostgreSQL aggregates when repeated dashboard scans materially
   affect chat latency or database CPU/I/O. This lowers read cost, but aggregation
   on the primary is not free offloading.
2. Introduce Cloud Scheduler → Cloud Run job → DuckDB only when a measured extract
   can serve multiple analyses or required history. Use the same region, bounded
   memory/runtime/concurrency and a least-privilege extraction role. Close the
   database connection before expensive analysis.
3. Extract only typed required columns, not whole session JSON or profiles. Use
   date-partitioned private Parquet and avoid tiny per-school files. Log sinks
   produce delayed JSON batches, not ready-made Parquet or a real-time feed; a
   job must validate, deduplicate and compact them before analysis.
4. Publish small aggregates to PostgreSQL for application reads. A local DuckDB
   file is a disposable job artifact, not a shared datastore serving requests.

[Scheduled Cloud Run jobs](https://docs.cloud.google.com/run/docs/execute/jobs-on-schedule),
[Cloud Storage log exports](https://docs.cloud.google.com/logging/docs/export/storage)

## Correctness, retention and privacy

Store counts and matching denominators, source cutoff, computation time, run ID,
metric version and completeness state. Keep daily activity metrics separate from
session-start cohort metrics. Weekly sums of overlapping distinct-session counts
are not weekly distinct counts. Preserve current UTC cohort semantics until a
timezone/rebucketing decision is explicit.

Incremental jobs need a reliable change marker for late feedback, corrections and
deletions, not just session start time or a fixed seven-day lookback. Recompute
affected cohorts, periodically reconcile retained history, prevent overlapping
runs and atomically publish validated replacements plus their checkpoint. Retries
replace partitions/upsert totals rather than increment them. Partial exports must
not appear in the current manifest. Retain the last successful report with a stale
indicator; do not fall back to an unbounded live scan after a job failure.

Flow/session deletion currently affects live history. Retained extracts would
change that behaviour and must support deletion propagation, partition rebuilding,
aggregate correction and expiry of previous versions. Suppressed data must not
be recoverable from totals, alternative filters or adjacent windows. The existing
five-session threshold is not a guarantee of five distinct people or anonymity.
Organisation rollups need the same differencing analysis as site reports.

Proposed retention classes, **requiring approval before activation**:

| Data | Proposed retention | Purpose |
| --- | --- | --- |
| New product observation logs / raw extracts | 30 days | Validate measurements and reconcile daily results |
| Pseudonymous linkage needed for corrections | At most 90 days, only if approved | Late updates/deletion; do not collect for anonymous cover metrics |
| Approved coarse product aggregates | 13 months | School-year comparisons; still subject to suppression and deletion assessment |
| Operational logs/traces and business/audit records | Existing separately governed policies | Do not silently change these with product analytics |

Do not claim these policies are enforced by this proposal. Validate actual bucket
retention, exclusions, access and backup/version behaviour before rollout. Durable
records linked to sessions remain pseudonymous, not anonymous. Lifecycle rules
are cleanup mechanisms, not a substitute for deletion/correction logic.

## Cost controls and cost per active customer

Protect the small Cloud SQL primary first. Measure extraction rows/bytes, query
plans/buffers, database CPU/I/O, connection occupancy and concurrent chat latency.
Cap analytics concurrency and abort on resource/latency guardrails agreed from
baseline measurements. Record job freshness, duration, failures, duplicate drops,
invalid observations and rejected batches without logging payloads or tokens.

Before adding a pipeline, measure a representative week and extrapolate event
volume × encoded bytes, ingestion/storage charges, job CPU/memory duration,
Scheduler, object operations and networking. Include shared free-tier consumption
and fixed database costs; no claim of free operation or savings without evidence.
Set a monthly analytics budget and volume alerts before enabling broader collection.

Report two cost views in one currency and calendar period:

- **Fully allocated cost per active site/organisation:** actual attributable
  hosting, database, storage, monitoring and delivery costs divided by the defined
  active population. Allocate shared infrastructure explicitly; show staging/CI
  separately and optionally include them in a total operating-cost view.
- **Marginal usage cost:** incremental requests, compute, bytes and delivery per
  additional session/site. Do not divide fixed SQL spend by requests and call it
  marginal cost. Payment fees and revenue should be reported separately.

Show actual versus estimated costs and the allocation method. With no active
sites, cost per active site is unavailable, not zero. Distinguish paying, trial,
complimentary and inactive customers without redefining usage around billing.

## Delivery sequence and acceptance gates

1. Inventory visible metrics against source queries; remove retired synthetic features end to end.
   Agree the catalogue, ownership, active-customer definition and freshness states.
   No new retention or infrastructure is needed for this step.
2. Introduce the shared typed observation module and browser helper. Migrate timing
   and implement cover observations as its first two consumers. Test exact,
   alternative, placeholder, failed image, offscreen/background, rerender, expiry,
   replay, invalid payload and telemetry failure. Verify no private fields in logs,
   native request paths or traces. Keep telemetry outside customer-journey SLOs.
3. Add bounded metrics/dashboards in Terraform. Separate deployment environments;
   report sampling/counts and missing-data states. Check log volume, cardinality,
   latency and billing after rollout. Disable telemetry independently of product UX.
4. After retention/budget approval, shadow a daily aggregation job against existing
   Insights. Test retries, old resumed sessions, deletions, changing site membership,
   UTC boundaries and suppression. Publish only after parity is understood.
5. Add Parquet/DuckDB only after a measured prototype demonstrates useful retained
   analysis or lower total primary load at an acceptable operating cost. Preserve
   the educator interface rather than coupling it to an analytical engine.

Review gates: retention/deletion policy and budget require product-owner approval;
metric contracts, privacy tests and load measurements require engineering review.
This proposal chooses a direction, not permission to ship all five stages at once.
