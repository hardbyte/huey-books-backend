# Architecture Roadmap

> **This document describes design vision and planned work, not current state.**
> For the current implemented architecture, see [architecture-service-layer.md](architecture-service-layer.md).

## Unit of Work Pattern Adoption

### Starting point

`app/services/unit_of_work.py` defines `UnitOfWork` and `SQLUnitOfWork` with lazy-loaded repository properties. Its existence does not require adoption: service-owned `commit()` and `flush()` remain a supported transaction boundary. Evaluate callers and repository compatibility before introducing it into a workflow.

### Design Intent

The Unit of Work pattern provides a single transaction boundary for write operations:

```python
async with self.uow:
    flow = await self.uow.flow_repo.get_by_id(flow_id)
    published = await self.uow.flow_repo.publish(flow, options)
    await self.uow.outbox_repo.add_event(FlowPublishedEvent(flow_id=flow_id))
    await self.uow.commit()  # atomic: business data + event in one commit
```

Benefits over manual transaction management:
- Explicit transaction boundaries (context manager)
- Atomic business data + event outbox writes
- Rollback on exception without manual error handling
- Cleaner separation between service logic and persistence plumbing

### Adoption criteria

Adopt during a service refactor only when the abstraction makes atomic writes,
rollback and dependency ownership clearer. Keep the request's existing session;
do not introduce a second connection or nested transaction owner. Prove that
business writes and outbox events roll back together. Read-only services still
use database transactions but do not need a write-oriented Unit of Work merely
to run a query.

## CQRS-Lite Evolution

### Design Direction

**Read side**: Query services use repositories without an explicit write/commit
workflow. SQLAlchemy still starts a database transaction for ordinary reads;
read-only does not mean transaction-free. Repositories perform SQL aggregation
and filtering; services define metric semantics and disclosure policy.

**Write side**: Command services coordinate repository writes and required outbox events in the same transaction. A Unit of Work is one possible implementation, not a prerequisite for atomicity.

The full CQRS pattern (separate read models, event-driven projections) is not planned. The "Lite" approach -- separating read and write services with different transaction strategies -- provides most of the benefit without the complexity.

### Next Steps

- Apply the read/write separation pattern to new services as they're created
- No need to retrofit existing working services unless they're being refactored for other reasons

## Remaining CRUD-to-Repository Migration

### User Domain (Highest Complexity)

Migrating `app/crud/user.py` requires preserving:
- Polymorphic user types via joined-table inheritance (Student, Educator, Parent, SchoolAdmin, WrivetedAdmin)
- Deeply integrated with authentication (`app/api/auth.py`, `app/api/dependencies/security.py`)
- Used in test fixtures (`conftest.py`)

**Migration strategy**:
1. Define a domain-oriented repository surface supporting polymorphic queries; add a Protocol if needed for substitution
2. Start with reads such as `get_by_id` and `get_by_email`; treat `get_or_create` as a write with concurrency and transaction requirements
3. Migrate authentication-related consumers carefully (security-critical code)
4. Migrate write operations and profile management
5. Update test fixtures last (they commit explicitly for HTTP request isolation)
6. Remove the old implementation and imports once callers migrate; do not retain a deprecated delegation layer

**Key risk**: The joined-table inheritance model means repository methods must handle type-specific queries (e.g., "get all students for school X") alongside generic user queries.

### Collection Domain (Medium Complexity)

`CollectionRepository` provides the destination for consumers of `app/crud/collection.py`. Preserve access scope, collection identity, transaction ownership and import behaviour while moving callers; matching method names alone do not establish equivalence.

### Event Domain (Medium Complexity)

Move consumers of `app/crud/event.py` to `EventRepository` without changing event attribution or commit timing. Application activity events and delivery-outbox entries have different purposes.

### API Layer Migration

As domains complete their CRUD-to-repository migration, update corresponding API endpoints to use service methods instead of direct CRUD/repository access. Find remaining consumers with `rg 'app\.crud|from app import crud' app` rather than maintaining usage counts here.

## Event System Evolution

### Current Architecture

Three event systems serve different purposes (see [architecture-service-layer.md](architecture-service-layer.md#event-systems)):
1. Application events (`events` table) -- editorial and domain activity, not a general telemetry sink
2. Chat flow events (NOTIFY/LISTEN) -- real-time dashboard updates
3. Event Outbox (`event_outbox` table) -- reliable delivery with retry

### Potential Improvements

**Webhook registration API**: The `WebhookNotifier` service delivers webhooks internally, but there are no user-facing endpoints for managing webhook subscriptions. A webhook registration API would allow external systems to subscribe to flow events without code changes.

**Event routing**: As the event outbox handles more delivery channels (Slack, email, webhook, internal), a routing layer could map event types to delivery channels declaratively rather than in code.

**Broadcast / segmented email** *(shipped)*: `app/services/broadcast.py` sends staff announcements to a user segment (account types, country, school) through the Event Outbox, with RFC 8058 one-click unsubscribe (`users.marketing_opt_out`). Recipient resolution currently queries the user domain directly; it will move behind `UserRepository` when that domain is migrated.

**Slack delivery**: `handle_event_to_slack_alert` in `app/services/events.py`
already delegates to reliable delivery through the event outbox. Preserve that
transaction boundary when migrating remaining callers.

**Observability and analytics**: Use the [OTel-first observability design](observability-architecture.md)
for operational signals and the [analytics architecture](analytics.md) for
product metric definitions and optional retained reporting. Database-backed
session replay remains a separate support feature. Do not route browser views
through the business outbox or treat sampled traces as a business ledger.

## Testing Strategy

### Coverage requirements

Assess coverage against the behaviour being changed, not suite size. Keep test
run results with the change record; they are not enduring architecture facts.

**Service unit tests**: Mock repository interfaces (ABC/Protocol), test business logic in isolation. Priority targets:
- `CMSWorkflowService` (publishing and visibility rules)
- `FlowService` (complex snapshot/publish logic)
- `ConversationService` (session lifecycle state machine)

**Repository integration tests**: Exercise PostgreSQL persistence semantics with a real database, including:
- `EditionRepository.create_in_bulk()` -- ISBN deduplication
- `WorkRepository.get_or_create()` -- complex matching
- `BooklistRepository.reorder_items()` -- authority system logic

**Concurrency tests**: `app/services/concurrency_service.py` implements advisory locks + revision control. Needs tests for:
- Concurrent session updates from multiple processes
- User interaction vs background timeout race conditions
- Advisory lock timeout scenarios

**Event outbox resilience tests**: Retry with exponential backoff, dead letter queue behavior, event ordering guarantees.

## Migration Workflow Reference

For CRUD-to-repository migration:

1. **Analyze** existing CRUD file
2. **Create a domain repository** with a narrow interface; add a Protocol or ABC only where useful
3. **Update consumers** through service boundaries, preserving transaction ownership and authorization
4. **Handle circular imports** (replace CRUD imports, extract utils to `app/utils/`, or use local imports)
5. **Delete CRUD file** once all consumers migrated
6. **Run full test suite** (`bash scripts/integration-tests.sh`)

Common issues: missing methods discovered by tests (`get_or_404`, `apply_pagination`), type handling flexibility (`ModelType | dict`), import duplicates from automated sed.
