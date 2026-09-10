# Architecture contracts and documentation guide

Use this guide to reconcile overlapping design documents. Documentation describes
contracts and design intent, not deployment status. Check migrations, runtime
code, tests and release records before assuming a proposal is implemented or live.

## Guidance and precedence

- `ADR.md` and `architecture-service-layer.md`: domain-oriented persistence,
  service-owned transactions, domain errors translated at the HTTP boundary.
  `architecture-roadmap.md` distinguishes proposed Unit of Work/CQRS changes
  from current practice. A read still has a database transaction; no explicit
  write workflow is needed simply to run a query.
- The organisation testing/entitlement docs describe current compatibility
  behaviour. ADR0003, `organisation-target-schema.md` and its migration plan
  describe the destination and separately gated cutovers. Empty identity tables
  are not a completed data migration.
- `school-billing.md` supersedes historical subscription examples in Stripe,
  signup and invitation docs. Verified payment is distinct from complimentary
  reader access. Organisation membership neither assigns subscriptions nor
  grants billing or child-record permissions.
- `identifier-naming.md`, `ai-assisted-labels.md` and `librarian-mcp.md` preserve
  legacy identifiers, reviewed-label provenance and separate REST/OAuth grants.
  No schema refactor may silently broaden machine scope or reset manual reviews.
- School Insights and ADR0001 retain bounded, private operational reporting.
  The analytics proposal is not permission to export or retain child activity.
- CMS, node schemas, chatbot, composite, campaign, replay and theme docs contain
  mixed historical examples and planned capabilities. Their schema examples are
  not migration input or sufficient evidence that a capability is deployed.
  Preserve normalized flow storage, original session attribution, content
  visibility and presentation/runtime separation through library migration.
- README, seed/testing docs and trace-correlation guidance govern local
  verification and operations. Use isolated synthetic data and ordinary tokens;
  never deploy a testing authentication bypass or expose credentials in logs.

## Organisation workspace contracts

Organisation HTTP handlers delegate to services. The compatibility repository
owns scoped discovery, batched summaries, membership queries, entitlement
evidence and locked holding upserts. Services retain capability policy, import
preview/conflict decisions and commits. Persistence methods do not commit or
raise HTTP exceptions. The request supplies the database session; no additional
connection, network call or generic CRUD framework is introduced.

Library manager removal and demotion serialize on the library row and retain an
active manager unless an authorised organisation manager or platform staff takes
responsibility. Home-administrator rights remain a separate access source.
Membership changes do not alter legacy user subtypes or home schools.

Invalid token subjects fail authentication with a Bearer challenge instead of
reaching account lookup/parsing. Role and resource authorization still use live
database state; View As remains read-only. Structural regression tests protect
the route/service/repository separation alongside HTTP and PostgreSQL tests.

The school trigram index is declared in the model as well as its historical DDL.
Model declarations must match deployed objects without rebuilding them unnecessarily.
New organisation/library/education identities use database UUIDv7 defaults;
existing identifiers are unchanged. Migration snapshots remain independent of
live application imports, with four explicit historical exceptions tracked by
the migration guard.

## Separately gated decisions

- Explicit legacy identity mapping, writer fencing, inventory/education/billing
  backfills and destructive contraction require the gates in
  [the schema migration plan](organisation-schema-migration.md). Any proposed
  repair to an executed migration requires separate review and replay-equivalence
  evidence; it must not silently change an existing database's migration history.
- The generic CMS/chat docs disagree about composite execution, script support,
  theme inheritance and some analytics implementation status. Resolve each
  against its runtime, schema and first-party UI tests before using it to change
  behaviour; do not implement an old example merely to make documentation true.
- Durable membership auditing and the broader education/billing membership
  model are not completed by structured operation logs. Repository extraction
  does not implement every capability in the target schema.
- Unit of Work adoption and a new persistence adapter remain optional follow-up
  designs, not prerequisites for the current service-owned transactions.

## Maintaining this guide

Keep contracts here and detailed procedures in their owning documents. Record
test results, benchmark samples, branch names and rollout evidence in change or
release records rather than treating them as evergreen guarantees.

When changing a contract, update its owning document and regression tests together.
The architecture and migration snapshot guards live in
`app/tests/unit/test_workspace_architecture.py` and
`app/tests/unit/test_migration_snapshots.py`. Permission, concurrency and database
behaviour also require PostgreSQL integration tests; structural checks alone do
not establish correctness. See [workspace testing](organisation-libraries-testing.md)
for role-based checks and [README](../README.md) for test commands.
