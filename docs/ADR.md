
## Decision Log (ADR-lite)

### ADR-001: Repository Pattern over Generic CRUD

**Status**: Accepted/Implemented  
**Date**: 2025-08-08  
**Context**: Current generic CRUD pattern encourages "one function per query" leakage and doesn't express domain concepts clearly.

**Decision**: Adopt domain-oriented repositories with Protocol interfaces instead of generic CRUD classes.

**Alternatives Considered**:
- Keep generic CRUD with better organization
- Move to raw SQL with query builders
- Adopt micro-ORM approach

**Trade-offs**:
- **Pros**: Clear domain boundaries, better testability, less query leakage
- **Cons**: More upfront interface definition work, learning curve for team

**Rationale**: Domain repositories express business intent and prevent data access layer from becoming a bag of random queries.

---

### ADR-002: CQRS-Lite over Full CQRS

**Status**: Proposed  
**Date**: 2025-08-08  
**Context**: Some operations are write-heavy (workflows), others are read-heavy (analytics). Full CQRS would be overkill.

**Decision**: Implement CQRS-Lite with surgical transaction management - write services use UnitOfWork, read services are transaction-free.

**Alternatives Considered**:
- Full CQRS with separate read/write databases
- Single transaction boundary for all operations
- No separation between reads and writes

**Trade-offs**:
- **Pros**: Performance optimization for read-heavy operations, clear separation of concerns
- **Cons**: More complex service architecture, need to manage two patterns

**Rationale**: Analytics reads don't need write transaction overhead, but workflows need ACID guarantees.

---

### ADR-003: Event Outbox over NOTIFY/LISTEN for Critical Events

**Status**: Accepted/Implemented  
**Date**: 2025-08-08  
**Context**: NOTIFY/LISTEN is unreliable for critical business events (8KB limit, no persistence, unreliable delivery).

**Decision**: Dual event strategy - keep NOTIFY/LISTEN for dev UX, add Event Outbox pattern for critical business events.

**Alternatives Considered**:
- Replace NOTIFY/LISTEN entirely with external message queue
- Keep NOTIFY/LISTEN for all events
- Move to Cloud Pub/Sub immediately

**Trade-offs**:
- **Pros**: Reliable delivery, no size limits, durability guarantees
- **Cons**: More infrastructure complexity, background daemon required

**Rationale**: Critical business events (flow published, session completed) need delivery guarantees. NOTIFY/LISTEN good for immediate dashboard updates.

---

### ADR-004: Advisory Locks over Optimistic Locking for Session State

**Status**: Accepted/Implemented  
**Date**: 2025-08-08  
**Context**: Session state has multiple concurrent writers (user interactions, background tasks, webhooks). Optimistic locking leads to lost updates.

**Decision**: Use PostgreSQL advisory locks with revision control for session state mutations.

**Alternatives Considered**:
- Pure optimistic locking with retry logic
- Distributed locks (Redis, etcd)
- Session state as immutable event stream

**Trade-offs**:
- **Pros**: Prevents data corruption, clear conflict resolution rules
- **Cons**: Additional complexity, potential deadlocks if misused

**Rationale**: Session state conflicts are common (user clicking while timeout processing). Advisory locks prevent silent data corruption.

---

### ADR-005: Service Layer Exceptions over HTTP Exceptions

**Status**: Accepted/Implemented  
**Date**: 2025-08-08  
**Context**: Current error handling mixes HTTP concerns with business logic, making services hard to test and reuse.

**Decision**: Services raise domain-specific exceptions. API layer converts to appropriate HTTP responses.

**Alternatives Considered**:
- Services return Result<T, E> types
- Services throw HTTP exceptions directly
- Error codes/status in return values

**Trade-offs**:
- **Pros**: Clean separation, easier testing, reusable across different interfaces
- **Cons**: More exception hierarchy to maintain, translation layer needed

**Rationale**: Service layer should be protocol-agnostic. Business errors should be expressed in domain terms, not HTTP terms.

---

### ADR-006: Node Processors as Pure Functions

**Status**: Proposed  
**Date**: 2025-08-08  
**Context**: Current node processors have database access and side effects, making them hard to test and reason about.

**Decision**: Node processors must be pure functions with no database access. Only ConversationService may persist changes.

**Alternatives Considered**:
- Allow node processors to write directly to database
- Node processors return commands for ConversationService to execute
- Split into pure computation + side effect phases

**Trade-offs**:
- **Pros**: Highly testable, predictable behavior, clear responsibility boundaries
- **Cons**: More coordination between runtime and service layers

**Rationale**: Pure functions are easier to test, debug, and reason about. Centralized persistence enables proper transaction and concurrency control.

---

### ADR-007: Resend over SendGrid for Email, behind an EMAIL_PROVIDER abstraction

**Status**: Accepted/Implemented
**Date**: 2026-07-16
**Context**: We needed to switch email provider away from SendGrid. All email flows through the Event Outbox, but the delivery adapter was hard-wired to the sync SendGrid SDK (a blocking call on the async event loop) and depended on SendGrid-hosted dynamic templates, so there was no clean way to swap providers or render templates ourselves.

**Decision**: Introduce an `EMAIL_PROVIDER` config that selects the delivery backend, add a Resend backend that sends via `httpx.AsyncClient` (async, non-blocking), and port the SendGrid dynamic templates to inline HTML rendered in-repo (`app/services/email_templates.py`). Keep the SendGrid path behind the flag as a reversible fallback.

**Alternatives Considered**:
- Reactivate/settle the SendGrid account (terminated accounts are rarely reinstated; keeps a vendor we're leaving).
- Amazon SES (cheaper at volume, more setup, not on our stack).
- Hard cut to Resend with no abstraction (no rollback path).

**Trade-offs**:
- **Pros**: No vendor lock, reversible cutover, non-blocking sends, templates are versioned in-repo and testable.
- **Cons**: We now own template HTML/copy; two backends to keep working while the flag exists.

**Rationale**: An abstraction with a config switch made the cutover low-risk and reversible, and moving delivery to async HTTP removed a loop-blocking call. Resend fits the low volume and de-Wriveting direction. See `docs/school-self-serve-signup.md` and `app/services/email_notification.py`.

---

### ADR-008: Payment Gates School Activation (Stripe Checkout + webhook)

**Status**: Accepted/Implemented
**Date**: 2026-07-16
**Context**: Schools self-register (`/onboarding/school`) into a PENDING state that previously required a manual approval step to activate. We want activation to be self-serve and gated on payment. The Stripe webhook already models SCHOOL subscriptions and resolves schools from `client_reference_id`, but there was no way to *initiate* a school checkout and nothing linked payment to activation. Signup alerts now go by email rather than Slack.

**Decision**: Payment gates activation. `POST /school/{id}/checkout` creates a Stripe Checkout Session (subscription mode) scoped to the school via `client_reference_id`; the `checkout.session.completed` webhook activates the school **only when `payment_status == "paid"`**. Cancellation (`customer.subscription.deleted`) deactivates the school (resolved from our `Subscription.school_id`, since the school isn't in that payload). Because the checkout link is payer-agnostic, a sponsor (parent/library) can pay it — the subscription attaches to the school regardless of payer.

**Alternatives Considered**:
- Approval-first (manual gate) — adds a manual bottleneck we want to avoid for self-serve.
- SetupIntent / charge-in-arrears — more moving parts than a flat recurring price needs.
- Activate on `checkout.session.completed` unconditionally — unsafe: completes unpaid for trials, 100%-off promos, and async payment methods.

**Trade-offs**:
- **Pros**: Fully self-serve, reuses existing webhook/subscription model, sponsorship falls out for free, no manual step.
- **Cons**: Access is only as correct as our webhook handling; activation/deactivation is idempotent-on-retry rather than single-transaction (accepted — see doc).

**Rationale**: A Stripe-hosted Checkout Session keyed to the school is the closest fit to the existing integration and the simplest correct way to gate access on real payment. Renewal reminders ride `invoice.upcoming`. See `docs/school-self-serve-signup.md`.
