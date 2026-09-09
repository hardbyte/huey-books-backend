# Rolling out library collections

This procedure covers the School-backed compatibility implementation, not the
physical library/education/billing cutovers in the [schema migration plan](organisation-schema-migration.md).

## Preconditions

- Use PostgreSQL 18 for runtime and migration tests; identity defaults use `uuidv7()`.
- Review historical replay repairs separately in PR #754. Existing revision IDs
  are not rerun or restamped. Original source fixtures and PostgreSQL equivalence
  tests protect successful data/constraint outcomes; transaction ownership moves
  to Alembic rather than a migration-local ORM session.
- Inspect institutional collection counts/defaults. The initial organisation
  migration refuses multiple legacy collections. The rolling compatibility
  migration refuses multiple collections without a default rather than choosing
  one. Reconcile ambiguous ownership explicitly; never delete holdings to pass
  a migration. Confirm backup/restore readiness before release.

## Expand, deploy, then enable

1. Keep `MULTIPLE_COLLECTIONS_ENABLED=false` (the default). Apply migrations before
   new code. Migration `0a6db7f4e561` installs a frozen snapshot of the declarative
   function/trigger and removes the database's false default. Under a bounded
   table lock it repairs only sole institutional collections missing their default.
   Ambiguous libraries stop the migration. A lock timeout requires a safe retry,
   not removal of the locking safeguard.
2. Deploy compatible public/internal APIs and background jobs, still with the
   switch off. Existing single-collection reads, imports and replacement remain
   available. First collection creation is allowed; additional collection
   creation returns 409, including for platform staff.
3. Verify old API revisions have no traffic or active requests and old jobs/tasks
   cannot execute. Old readers assume a single inventory, so installing the
   trigger alone is not sufficient. Check legacy imports and new scoped reads
   against the same libraries before enabling the feature.
4. Enable `MULTIPLE_COLLECTIONS_ENABLED=true` on compatible serving revisions.
   Deploy/test the matching admin UI. Exercise import preview/confirm, scoped
   review, direct and organisation grants, expiry and read-only View As.
5. Watch errors, lock timeouts, request latency and default-inventory invariants.
   Only group libraries and associate subscriptions with verified ownership.

## Database compatibility contract

An omitted `is_default` means an old writer. The BEFORE INSERT trigger locks the
owning school row. An empty institutional library gets a default collection;
a personal collection gets `false`. An omitted flag with existing institutional
collections is rejected rather than creating an ambiguous inventory. Explicit
new `true`/`false` values are preserved; the partial unique index prevents two
defaults. Legacy delete-and-recreate replacement of a sole collection retains
the default. Concurrent inserts cannot create two legacy inventories.

The ORM keeps its explicit `false` default for additional collections. Do not
restore a database `false` default while old-writer compatibility is needed:
it would erase the distinction between omitted and explicit values.

## Rollback

Before additional inventories exist, compatible application rollback can retain
the expanded schema and trigger. After they exist, disable further creation if
necessary, but do not route traffic to single-collection readers. Roll back only
to a multi-collection-compatible revision, or roll forward with a fix. The switch
does not hide or remove existing inventories. Schema downgrades refuse data loss;
never force them or stamp past that refusal.

## Verification

`test_collection_rolling_compatibility.py` covers old/new inserts, replacement,
concurrent admission, catch-up, ambiguity refusal, downgrade/re-upgrade and
declarative snapshot equality. Workspace HTTP tests cover the default-off gate.
Rehearse fresh replay and populated upgrades in an isolated database; keep the
results with the release rather than treating this procedure as deployment evidence.
