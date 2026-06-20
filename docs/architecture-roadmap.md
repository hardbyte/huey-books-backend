# Architecture Roadmap

> **This document describes design vision and planned work, not current state.**
> For the current implemented architecture, see [architecture-service-layer.md](architecture-service-layer.md).

## Unit of Work Pattern Adoption

### Current State

`app/services/unit_of_work.py` (128 lines) defines `UnitOfWork` (ABC) and `SQLUnitOfWork` with lazy-loaded repository properties. The implementation is complete but has zero production usage -- services manage transactions manually with `db.commit()` and `db.flush()`.

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

### Adoption Plan

1. Refactor `FlowService` to use UoW (lowest risk -- already has clean service boundaries)
2. Refactor `CMSWorkflowService` and `ConversationService`
3. Evaluate whether read-only services like `AnalyticsService` benefit (likely no -- they don't need transactions)

### Why It Hasn't Been Adopted

Services were built before UoW was finalized. Manual transaction management works reliably and the migration cost exceeds the immediate benefit. Worth adopting during the next service refactoring cycle rather than as a standalone task.

## CQRS-Lite Evolution

### Current State

Four services demonstrate intentional read/write separation:
- `AnalyticsService` -- read-only, no transaction overhead
- `CMSWorkflowService` -- write operations with event publishing
- `ConversationService` -- session lifecycle management
- `FlowService` -- flow CRUD with snapshot regeneration

Most other service files (~44 out of 48) mix reads and writes without architectural separation.

### Design Direction

**Read side**: Query services access repositories directly without transactions. Business calculations happen in the service layer, not in SQL queries. This is already demonstrated in `AnalyticsService`.

**Write side**: Command services coordinate repository writes + event outbox publishing within a single Unit of Work. This pattern ensures atomicity between business data and events.

The full CQRS pattern (separate read models, event-driven projections) is not planned. The "Lite" approach -- separating read and write services with different transaction strategies -- provides most of the benefit without the complexity.

### Next Steps

- Apply the read/write separation pattern to new services as they're created
- No need to retrofit existing working services unless they're being refactored for other reasons

## Remaining CRUD-to-Repository Migration

### User Domain (Highest Complexity)

`app/crud/user.py` (337 lines) is the last major unmigrated domain:
- 39 import usages across 16 files
- Polymorphic user types via joined-table inheritance (Student, Educator, Parent, SchoolAdmin, WrivetedAdmin)
- Deeply integrated with authentication (`app/api/auth.py`, `app/api/dependencies/security.py`)
- Used in test fixtures (`conftest.py`)

**Migration strategy**:
1. Create `UserRepository` with Protocol interface supporting polymorphic queries
2. Start with read operations (most common: `get_by_id`, `get_by_email`, `get_or_create`)
3. Migrate authentication-related consumers carefully (security-critical code)
4. Migrate write operations and profile management
5. Update test fixtures last (they commit explicitly for HTTP request isolation)
6. Keep `crud/user.py` as deprecated delegation layer during transition

**Key risk**: The joined-table inheritance model means repository methods must handle type-specific queries (e.g., "get all students for school X") alongside generic user queries.

### Collection Domain (Medium Complexity)

`app/crud/collection.py` (210 lines) -- 26 usages across 9 files. `CollectionRepository` already exists. Migration is mechanical: update imports and adjust method signatures.

### Event Domain (Medium Complexity)

`app/crud/event.py` (151 lines) -- 49 usages across 16 files. `EventRepository` already exists. Higher usage count but the operations are simpler.

### API Layer Migration

~16 API files still import from `app.crud`. As domains complete their CRUD-to-repository migration, the corresponding API endpoints should be updated to use service layer methods instead of direct CRUD/repository access. CMS, analytics, and chat endpoints already demonstrate this pattern.

## Event System Evolution

### Current Architecture

Three event systems serve different purposes (see [architecture-service-layer.md](architecture-service-layer.md#event-systems)):
1. Application events (`events` table) -- user activity, monitoring
2. Chat flow events (NOTIFY/LISTEN) -- real-time dashboard updates
3. Event Outbox (`event_outbox` table) -- reliable delivery with retry

### Potential Improvements

**Webhook registration API**: The `WebhookNotifier` service delivers webhooks internally, but there are no user-facing endpoints for managing webhook subscriptions. A webhook registration API would allow external systems to subscribe to flow events without code changes.

**Event routing**: As the event outbox handles more delivery channels (Slack, email, webhook, internal), a routing layer could map event types to delivery channels declaratively rather than in code.

**Broadcast / segmented email** *(shipped)*: `app/services/broadcast.py` sends staff announcements to a user segment (account types, country, school) through the Event Outbox, with RFC 8058 one-click unsubscribe (`users.marketing_opt_out`). Recipient resolution currently queries the user domain directly; it will move behind `UserRepository` when that domain is migrated.

**Slack alert migration**: `handle_event_to_slack_alert` in `app/services/events.py` still uses direct Slack API calls. Migrating to `SlackNotificationService` via the event outbox would provide retry logic and better testing.

## Testing Strategy

### Current Gaps

- 44 of 48 service files lack dedicated unit tests (tested indirectly through 638+ integration tests)
- No repository unit tests (complex logic like `EditionRepository.create_in_bulk` with 223-line deduplication is only tested via API endpoints)
- No concurrency-specific tests (advisory locks, revision conflicts, background task race conditions)

### Recommended Approach

**Service unit tests**: Mock repository interfaces (ABC/Protocol), test business logic in isolation. Priority targets:
- `CMSWorkflowService` (29KB, zero unit tests)
- `FlowService` (complex snapshot/publish logic)
- `ConversationService` (session lifecycle state machine)

**Repository unit tests**: Priority for repositories with complex business logic:
- `EditionRepository.create_in_bulk()` -- ISBN deduplication
- `WorkRepository.get_or_create()` -- complex matching
- `BooklistRepository.reorder_items()` -- authority system logic

**Concurrency tests**: `app/services/concurrency_service.py` implements advisory locks + revision control. Needs tests for:
- Concurrent session updates from multiple processes
- User interaction vs background timeout race conditions
- Advisory lock timeout scenarios

**Event outbox resilience tests**: Retry with exponential backoff, dead letter queue behavior, event ordering guarantees.

## Migration Workflow Reference

The proven 6-step workflow for CRUD-to-repository migration (refined through 12 successful domain migrations):

1. **Analyze** existing CRUD file
2. **Create repository** with ABC interface + SQL implementation
3. **Update consumers** (imports + method calls)
4. **Handle circular imports** (replace CRUD imports, extract utils to `app/utils/`, or use local imports)
5. **Delete CRUD file** once all consumers migrated
6. **Run full test suite** (`bash scripts/integration-tests.sh`)

Common issues: missing methods discovered by tests (`get_or_404`, `apply_pagination`), type handling flexibility (`ModelType | dict`), import duplicates from automated sed.
