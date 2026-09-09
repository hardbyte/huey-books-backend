# Migration toward separate library and education ownership

Status: proposed implementation sequence. No production schema or data changes are authorised by this document.

Target: [physical model](organisation-target-schema.md). Existing [compatibility ADR](adr/0002-library-management-compatibility.md) remains the current implementation until each cutover is verified.

## Source-grounded hazards

`app/models/school.py` mixes integer identity, public UUID, library settings, admission settings and cascading relationships. Collections/classes/subscriptions use the public UUID; students, educators and other consumers use the integer. `app/models/student.py` and `class_group.py` do not enforce matching school scopes. `app/models/collection.py` permits ambiguous ownership.

FK inspection alone is insufficient: `app/models/oauth.py` has an unreferenced school scope, campaigns contain integer arrays, and chat context contains `school_wriveted_id`. `app/api/chat.py` prefers home-school context. `app/db/views.py` counts library reach using distinct collection school UUIDs. CMS and session attribution need explicit mapping. Billing's current organisation association does not remove legacy school deletion cascades.

## Independently reviewable steps

1. **Inventory and contracts.** Enumerate database FKs, ORM cascades, views, JSON/array identifiers, routes, machine grants, scheduled jobs and external integrations. Classify every legacy school explicitly; never infer organisation merges from names/domains. Keep customer-specific mapping outside this public repository. Record row counts, orphan/conflict reports and payment ownership evidence. Add contract tests for existing reader URLs and permission boundaries.
2. **Shared application seams.** Centralise library capability checks and catalogue/site/staff services behind typed library UUIDs. Reuse them from education navigation. Fix legacy home links without widening legacy school ACLs. Add robust optional location responses and regressions for the QA findings. This can ship independently of table migration.
3. **Add the destination.** Add organisations/libraries/education units/mapping and explicit scoped memberships. Preserve each existing public school UUID as its library UUID; allocate separate education identities only where needed. Initially preserve separate student namespaces. Backfill in restartable batches; one authoritative write path plus compatibility projection, never independent competing writers. First deploy a compatible writer to every revision/job and drain old writers before switching authority; if that cannot be guaranteed, use a narrowly scoped transactional compatibility trigger and test both revision orders. Backfill alone does not protect writes during rolling deployment.
4. **Move inventory and library consumers.** Add/backfill library FKs, indexes and constraints. Resolve ambiguous collection owners before enforcing XOR. Migrate defaults with same-library composite constraints. Migrate CMS, OAuth/machine scopes, booklists/events and recommendations according to the inventory, not a blanket substitution. Shadow-compare reads and deny unexpected access.
5. **Move education and staff.** Backfill education-unit classes/students and composite constraints; reconcile mismatches rather than dropping students. Replace home-school-derived grants with reviewed explicit memberships. Test multi-role users without destructive subtype promotion. Keep legacy authentication compatible until all account-type consumers are retired.
6. **Move billing separately.** Reconcile exact subscription owners and library coverage. Preserve provider IDs, family ownership, grace/comp expiry, invoice attempts and idempotency keys. Cut over webhook routing and locking to billing accounts with replay/out-of-order tests. Do not cancel, consolidate or charge subscriptions as part of schema migration.
7. **Switch and observe.** Enable scoped API/UI readers per deployment flag after reconciliation is clean. Preserve legacy URLs and JSON attribution. Compare recommendation inventories, permission denials, session attribution and bounded dashboard results. Deploy compatible code before activating new readers.
8. **Contract later.** Remove old writes/columns only after consumer inventory and telemetry show none remain and backup restoration is rehearsed. Do not collapse already-applied migrations. A reverse migration must refuse to discard new multi-library/education/billing data it cannot represent.

## Release gates

- Validate candidate DDL in a disposable PostgreSQL database: owner XOR, cross-organisation association rejection, class scope mismatch rejection, default-collection scope, financial deletion protection and all required indexes.
- Rehearse with representative synthetic fixtures: single school; junior/senior libraries; separate education units sharing a library; public branches without students; personal collections; multiple existing subscriptions; expired and complimentary service.
- Run old/new API contract tests and a negative permission matrix, including organisation manager vs billing/children, reviewer vs uploader, direct-library access after entitlement lapse, machine grants and View As.
- Browser-test import/confirm/edit quantities, global and local review, site editing and staff membership using actual scoped test identities, not only platform staff or read-only View As.
- Backfill counts and referential checks must match; preserve reviewed labels and session JSON byte-for-byte. Verify library-count analytics separately from organisation counts.
- Bound lock duration, batch backfills and validate constraints after backfill; concurrent indexes need their own transaction handling. Re-run application/read-only role checks after migrations, including existing materialized-view privileges.
- Test deployment order, rollback-compatible readers, signed webhook replay and database restore. No destructive contraction in the initial release.

## First implementation slice

Start with shared library capability/resource resolution and reusable library detail/staff/catalogue UI, with regression tests for the role-based workflows. Review additive identity/mapping DDL separately from consumer cutovers. Keep billing, education migration and destructive contraction as separate reviewed changes; no single change needs to contain the entire destination.

## Compatibility foundation

The initial foundation adds `Library`, `EducationUnit` and their same-organisation association in migration `d73a84c1b23e`. No mapping/backfill is performed and application reads/writes still use the compatibility model. The migration only adds three empty tables; downgrade refuses to remove populated identities. Archive timestamps and RESTRICT ownership prevent deletion cascades in the new model.

The compatibility workspace exposes `manage_details` and an optimistic name-only update. The librarian editor does not accept organisation, country, education settings or payment fields. Country editing requires separated storage because the legacy field participates in school identity. Educator home navigation uses the library workspace; absent school location metadata must be accepted by response validation.

The school page and library workspace reuse the same capability-gated library detail/access controls; bookbot settings remain separate. Migration `e84b95d2c34f` adds the cataloguer role: inventory writes without membership/detail management or review authority. Existing independent/home grants remain additive. Unknown role values fail closed.

An identity-table foundation does not establish legacy mapping and classification,
writer cutover fencing, scoped-role/subtype migration, collection ownership/default
migration, education records, or financial ownership/coverage migration. Each
requires independent reconciliation and release evidence before production cutover.

Verification uses the focused organisation HTTP tests, library browser suites, and `app/tests/integration/test_library_identity_schema.py`. The identity constraints run in the normal integration suite with transactional fixtures rather than requiring a separate opt-in. CI also runs migration checks and the full integration suite.
