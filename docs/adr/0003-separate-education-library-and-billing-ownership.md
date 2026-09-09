---
status: proposed
---

# Separate education, library and billing ownership

Legacy school records combine borrowing scope, student privacy and financial ownership, which cannot correctly represent multi-library schools or public-library branches. Target independent organisation, library and education-unit identities, with organisation/family billing accounts and explicit scoped memberships; retain public library UUIDs through compatibility mapping rather than renaming the entire school table. This costs a staged migration but avoids granting student/billing access through catalogue management or making a library own an organisation's subscription.

See the [physical model](../organisation-target-schema.md) and [migration plan](../organisation-schema-migration.md); ADR0002 remains the current compatibility implementation until cutover.
