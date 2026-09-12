# Session replay

Session replay is a PostgreSQL-backed support feature, separate from distributed
OpenTelemetry tracing. It can contain sensitive node state even after masking.
Do not export that state to Cloud Trace, diagnostic logs or product analytics.
Operational signals follow [observability architecture](observability-architecture.md).

## Implemented paths and gaps

| Path | Implementation |
| --- | --- |
| Models | `FlowExecutionStep`, `TraceAccessAudit` and session tracing fields in `app/models/cms.py` |
| Direct persistence / retrieval | `ExecutionTraceService.record_step` and read methods in `app/services/execution_trace.py` |
| Runtime capture | `chat_runtime.py` calls `record_step_async` when enabled; its buffer flush only logs intent and clears the buffer, without persisting or enqueueing work |
| Masking | `app/services/pii_masker.py` masks state in the direct write path; this is not an anonymity guarantee |
| Cleanup | `app/services/trace_cleanup.py` has batched trace/audit deletion methods; no mounted cleanup route or production invocation is established by this implementation |
| Viewer interfaces | Trace endpoints in `app/api/cms.py`, under the staff/backend-only CMS router |

The models, direct-write tests and viewer do not establish an end-to-end runtime
recording implementation. Do not describe buffered capture or scheduled expiry
as operational without verifying those paths.

## Data and access

Execution steps associate a session with node/step identifiers, before/after state,
execution details, connection decisions, timings and errors. Trace access audit
records can contain the staff user, session, IP address and user agent. These are
sensitive support records with different purposes from anonymous UX observations.

The CMS router requires staff/backend authority. Some endpoints also take the
current actor to record user access; absence of model-level ACLs does not mean
the router is public. Service-account reads need their own audit policy. Giving
educators access would require a separate school-scoped authorization design.

Current mounted paths, relative to `/v1/cms`:

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/flows/{flow_id}/sessions` | Session listing |
| GET | `/sessions/{session_id}/trace` | Stored execution steps; user trace reads create audit entries |
| GET / POST | `/flows/{flow_id}/tracing` | Tracing configuration |
| GET | `/flows/{flow_id}/trace-stats` | Stored trace aggregates |
| GET | `/trace-storage` | Storage statistics |

The schema distinguishes minimal, standard and verbose levels, but the current
`get_trace_level` returns `STANDARD`. Test configuration through capture before
claiming another level affects stored data. Likewise, `retention_days` and cleanup
methods do not establish enforced expiry: no `/internal/tasks/cleanup-traces`
route is mounted. Scheduling, failure monitoring and deletion verification remain
necessary before relying on a retention policy.

## Completion or removal decision

Replay is not required for OTel tracing or aggregate School Insights. Decide its
support value separately rather than implementing it as part of an exporter change.

- If retained, replace the non-persisting buffer path with bounded, privacy-tested
  capture and enforced cleanup. Prove runtime → masked persistence → authorized
  viewer → expiry, including service-account audit and failure paths.
- If retired, remove runtime hooks, routes, viewer controls, configuration and
  unused implementation together. Do not retain deprecated no-op methods. Removal
  of stored data or tables requires an explicit retention/migration decision;
  never edit already executed migrations to erase the feature's history.

Any future capture policy must specify who can enable it, allowed fields, maximum
size/rate, retention, deletion propagation and impact on student chat performance.
Masking arbitrary conversation state is not sufficient justification to retain it.
