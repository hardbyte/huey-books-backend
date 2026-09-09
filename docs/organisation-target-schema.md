# Organisation, library and education: target physical model

Status: proposed. This describes the destination, not the schema currently deployed. Implement through the separately gated [migration plan](organisation-schema-migration.md).

## Boundaries

An organisation is the administrative and commercial owner. A library is a borrowing/recommendation service, not necessarily a building. An education unit is the student/class privacy boundary: an actual school or independently administered division. These are separate identities even when a customer initially has one of each.

A school with junior and senior libraries can have one education unit and two libraries. Independently administered divisions can have separate education units within one organisation. A public-library organisation needs no education unit. Grouping organisations must never merge student username namespaces or grant access to children.

Do not introduce physical `sites` yet: no current workflow requires premises to have independent identity. Libraries have optional structured location fields. A future premises entity can serve several libraries without changing library identity.

## Physical tables and invariants

All new identities use UUID primary keys, timestamps use `timestamptz`, and referenced ownership keys are indexed. Existing work, edition, user and class identities remain unchanged. The following is a relational contract, not executable migration DDL.

| Table | Principal columns and enforced relationships |
| --- | --- |
| `organisations` | `id`, name, kind, lifecycle, timestamps; archive rather than cascade-delete |
| `libraries` | `id`, non-null `organisation_id`, name, lifecycle, optional location, operational settings; unique `(organisation_id, id)` |
| `education_units` | `id`, non-null `organisation_id`, name, country/official identifier, admission settings; unique `(organisation_id, id)` |
| `education_unit_libraries` | `(education_unit_id, library_id)` PK plus `organisation_id`; composite FKs to both owners prohibit cross-organisation links |
| `legacy_school_identity` | old integer PK, unique old public UUID, unique `library_id`, nullable `education_unit_id`; explicit migration mapping, no implicit access grant |
| `organisation_memberships` | `(organisation_id, user_id)` PK; explicit management role; not a billing or education grant |
| `library_memberships` | `(library_id, user_id)` PK; manager, cataloguer or reviewer role mapped to explicit capabilities |
| `education_memberships` | `(education_unit_id, user_id)` PK; explicit education role independent of library rights |
| `user_workspace_preferences` | `user_id` PK, nullable preferred library; preference grants no access and is revalidated on use |
| `collections` | existing UUID, nullable `library_id`, nullable personal `user_id`; CHECK exactly one owner; ownership FKs RESTRICT; unique `(library_id, id)` for the default-selection FK |
| `library_default_collections` | `library_id` PK, `collection_id`; composite FK `(library_id, collection_id)` to collections enforces same owner |
| `class_groups` | existing UUID, non-null `education_unit_id`; unique `(education_unit_id, id)` and `(education_unit_id, name)` |
| `students` | existing user UUID, non-null `education_unit_id`, existing class UUID; composite FK to class enforces same education unit; username unique within education unit |
| `billing_accounts` | UUID, exactly one of organisation or family payer user; partial unique indexes on each owner; ownership RESTRICT |
| `billing_account_customers` | `(provider, customer_id)` PK, billing account FK; several existing provider customers may belong to one account without consolidation |
| `billing_obligations` | UUID, billing account FK, stable legacy obligation key; invoices/checkout attempts retain obligation-scoped uniqueness rather than colliding after organisation grouping |
| `billing_memberships` | `(billing_account_id, user_id)` PK; explicit billing access, never inherited from catalogue management |
| `subscriptions` | existing provider subscription identity, non-null billing account/obligation; provider/customer references and payment history preserved; nullable organisation scope constrained to account owner; no library ownership FK |
| `subscription_library_coverage` | subscription/library pair plus non-null organisation scope; composite FKs to subscription and library enforce the same organisation; explicit reader-service coverage, independent from management entitlement |
| `organisation_entitlement_grants` | UUID, organisation, feature, validity interval, source kind, optional subscription FK, reason; source/interval checks; composite subscription/organisation FK; subscription-derived eligibility still requires current verified payment state |

The education-library association is deliberately many-to-many: a shared school library must not force merging educational privacy boundaries. This does not enable cross-organisation sharing; that would require a separate access agreement. Library staff do not gain education access through this association.

Collections retain edition-level holdings and existing quantities/uniqueness. An aggregated catalogue is a query over authorised collections, not duplicated stock. The default collection selects an integration fallback; it does not silently restrict recommendation scope. A library has an explicit recommendation mode (`all_collections` or `selected_collections`); selected collection IDs use same-library composite FKs. Import confirmation shows whether the destination participates in recommendations, and migration preserves the existing effective inventory rather than changing it implicitly. Global works, labels, evidence, review provenance and manually reviewed flags are unchanged.

Use RESTRICT for business ownership/history. Classes are archived; removing a populated class requires explicit student reassignment, never cascading student deletion. Deletion/anonymisation of personal records remains an explicit retention workflow, not an accidental consequence of deleting a library or class. Migrating existing deletion behaviour is separately gated.

## Identity, permissions and billing

User identity must not require an Educator subtype or one mandatory home school. Keep authentication identity separate from scoped memberships. During transition, legacy account types can coexist, but cannot manufacture organisation, library, billing or education grants. Moving a preferred library must not change rights. View As remains read-only, checks the target's effective scope and records the actual actor; it never borrows the actor's permissions.

Authorisation is the intersection of authenticated actor, resource scope, capability and any applicable feature entitlement. Multiple-library management remains included in existing paid school/library subscriptions; no new prices or charges. Preserve the existing lapse policy and direct-library access. Complimentary service does not accidentally become paid multi-library entitlement. Manual grants need explicit feature policy and audited reason, not fake Stripe subscriptions.

A billing account owns financial obligations. A subscription may cover selected libraries without being owned by one. Organisation grouping does not transfer or consolidate existing obligations, copy payment evidence or activate reader service at every branch. Existing provider IDs and webhook routing must survive cutover. Preserve one-open-attempt constraints per obligation, allowing several existing obligations under one account; lock the account when creating new coverage and reject overlapping purchases unless an explicit renewal/replacement workflow applies. Family billing remains separate.

For financial scope enforcement, billing accounts expose unique `(id, organisation_id)` and `(id, owner_kind)`, and subscriptions expose unique `(id, organisation_id)`. A non-null `(billing_account_id, owner_kind)` composite FK and a CHECK requiring organisation scope exactly when owner kind is organisation prevent exploiting nullable composite FKs. Organisation subscriptions also require their organisation to match the billing account through a composite FK; family subscriptions have no organisation and cannot satisfy non-null coverage/grant organisation FKs. Obligation/account and provider-customer/account pairs also require matching composite FKs. Do not rely on application checks alone for these ownership relationships.

Moving a library between organisations is an explicit transfer operation, not an ordinary PATCH: reconcile education links, direct/delegated memberships and subscription coverage atomically, require authority at both ends, and retain original historical attribution. Existing paid rights do not transfer automatically. Until that workflow exists, reject reassignment of linked libraries.

### Capability baseline for the first implementation slice

| Grant | Permitted scope | Explicit exclusions |
| --- | --- | --- |
| Organisation manager | Organisation identity, library management and library membership delegation, subject to entitlement | Billing grants, education grants, student records, staff-check publication |
| Library manager | Own library details, inventory and local membership management | Other libraries; organisation/billing/education roles |
| Library cataloguer | Import and edit own library holdings | Site details, staff grants, canonical label publication |
| Library reviewer | Discover local/shared catalogue and submit evidence-backed review proposals | Upload, staff grants, staff-check promotion |
| Education administrator | Own education unit and education memberships | Implied library or billing access |
| Billing administrator | Own billing account | Implied catalogue or student access |
| Platform reviewer | Authoritative shared-label publication under existing review policy | Automatic trust of imported/AI proposals |

Library managers may delegate up to their own library role, but cannot remove the final manager without an authorised organisation manager taking responsibility. Organisation membership never grants billing/education delegation. Review submission and authoritative publication are separate capabilities: preserve existing educator provenance and manual staff-check authority during migration; no imported proposal silently promotes a work to staff checked. Audit actor, target, scope and before/after state for membership and publication changes.

Entitlements are a local projection of verified payment/policy evidence. Do not add synchronous Stripe calls to requests or a second Stripe Entitlements integration while all paid products have the same feature. Re-evaluate that integration if the product catalogue diverges.

## Shared implementation and interfaces

- Library workspace owns catalogue upload, holdings, review entry points, library details and local staff. School screens reuse these components/services; they do not implement a second catalogue or staff editor.
- Education screens own classes, students and admission settings. Official school identifiers and verified admission domains live here. LMS integration credentials/configuration belong to the library integration, not to school identity.
- Organisation screens own grouping and organisation staff. Billing is a separately authorised area, not a nominated “billing library”.
- Work review is shared globally, with library-scoped discovery and explicit permission checks; opening a global work does not imply a right to edit it.
- Use `/libraries/{library_uuid}` for library operations and explicit collection UUIDs for inventory writes. Education APIs name education units. Legacy `/school/...` routes adapt to the appropriate boundary; do not broadly alias every school operation to a library.
- Reader chat selects an explicit authorised library. An authenticated user's home preference must not override the requested library. Admission, recommendation inventory and education identity are separate checks.
- CMS content, flows and themes retain existing library visibility. Organisation sharing is explicit, not automatic membership inheritance.
- Sessions retain immutable original attribution. Capture organisation-at-event separately for historical regional/organisation reporting; current organisation membership cannot rewrite history. Historical organisation is nullable/unknown unless supported by historical evidence, never backfilled from today's ownership. Preserve old chat JSON. Library reach and organisation reach are distinct metrics.

## Choices and open details

Reject a wholesale `schools` rename: it preserves the current ownership error. Reject a school profile attached one-to-one to a library: it fails the multi-library school case. Reject polymorphic `owner_type/owner_id` for core ownership: typed FKs and exactly-one-owner checks provide stronger integrity.

Before executable DDL: decide validated location/admission fields from the inventory, classify existing CMS/event/booklist scopes, audit personal collection ownership, and reconcile billing ownership/coverage. None requires changing the boundary model. The capability baseline above requires endpoint-level tests and review before implementation; retention policy and any capability expansion must not be inferred from migration data.
