# Setting up an organisation from an existing school

A school with one library can grow into an organisation with several. This is
the guided operation that makes that happen in one step, instead of creating an
empty organisation, attaching the school and assigning subscription ownership
separately.

## What it does

`GET /libraries/{library_uuid}/setup` reports whether the school can become an
organisation and who may manage it. `GET /libraries/{library_uuid}/setup/managers`
pages and searches the colleagues eligible to manage it. `POST` to the same path
creates, in one transaction: the organisation, its association with the school's
existing paid subscription, a second library with an empty default collection,
explicit organisation-manager grants, an optional librarian for the new library
only, the audit trail and the transactional invitation outbox.

The school is not renamed and not otherwise changed. Its catalogue, collection
identifiers, students, classes, chat links, Bookbot settings and existing access
all stay as they are. Organisation managers gain access to both libraries;
nothing existing is narrowed. Removing access remains a People & access
operation.

## Libraries and education units

`schools` is still the physical row behind a library, so every row now says
which of the two identities it carries through `schools.kind`:

| kind | Meaning | May hold |
| --- | --- | --- |
| `school` | An education unit that is also its own library. Every pre-existing row. | Students, classes, home staff, admission domains, official identifiers |
| `library` | A borrowing and recommendation service inside an organisation. | Catalogue, collections, library memberships, Bookbot settings |

The second library setup creates is a `library` row, as is every library created
through `POST /organisations/{id}/libraries`. A junior/senior split therefore
gets one education unit and two libraries, which is the boundary
[the target model](organisation-target-schema.md) describes; `library` rows are
the compatibility stand-in for `libraries` identities until that migration runs.

The separation is enforced in the database, not by convention. A CHECK keeps
admission domains and official identifiers off `library` rows, and
`require_education_unit_school` (see `app/db/triggers.py`) rejects students,
classes and home-staff assignments that point at one, on insert and on update.
The People page reflects this: educator and school-administrator roles are only
offered where the row is an education unit.

A `library` row has no subscription of its own and stays inactive. Reader access
comes from the organisation entitlement, so it follows the school's payment; see
[organisation entitlements](organisation-entitlements.md). Bookbot starts
disabled with local-catalogue-only recommendations, which is the default for an
inactive row — setup writes no chat settings, and the existing chat-settings
endpoint configures the rest once books are imported.

## Authority and payment

Platform staff and the source school's active administrators can perform setup.
A library-manager grant is catalogue authority, not payment authority, so it is
not enough on its own. View As is read-only and setup is not a read route.

A school administrator must include themselves among the organisation managers,
so they keep management of both libraries. Only colleagues who already manage
the source library may be chosen: its home staff, and anyone holding an explicit
library-manager grant. Reviewers and cataloguers are read or catalogue scoped and
are not offered.

Setup requires exactly one current verified paid school or library subscription
on the source library, with no existing organisation owner. The preview and the
conflict response both carry a machine-readable code:

| Code | Meaning |
| --- | --- |
| `already_grouped` | The library already belongs to an organisation |
| `no_paid_subscription` | No current verified paid subscription |
| `multiple_paid_subscriptions` | Ownership is ambiguous; support must confirm it |
| `subscription_already_owned` | Another organisation already owns the subscription |

Setup associates the exact subscription with the new organisation. Billing
customer, payment evidence, reader coverage and renewal behaviour are unchanged,
and no Stripe request, price or charge is made. Ordinary library attachment still
never transfers subscription ownership.

## Retries

The client supplies a `request_id` and the library name it reviewed. The request
is stored in `idempotency_records`, a shared table keyed by operation and client
key, so a retry after a lost response returns the original identifiers rather
than repeating the work. Reusing a key with different input, a different actor
or against a different library is a `idempotency_key_reused` conflict. Concurrent
submissions of the same key serialise on an advisory lock; different keys
serialise on the source row, and the loser sees `already_grouped`.

Any failure leaves no organisation, library, grant, invitation or receipt.

## Verification contract

Exercise a paid school administrator, platform staff, an unrelated
administrator, a library-only manager and View As. Cover unpaid, expired,
ambiguous and already-owned payment; a stale reviewed name; a colleague who is
no longer eligible; concurrent identical and differing submissions; and rollback.
Verify the source school's identifiers, holdings, access and chat settings are
unchanged, that the new librarian cannot reach the source library, and that
organisation managers can reach both. Verify the database rejects students,
classes and home staff on the new library. Browser tests cover desktop and
mobile, back and edit, validation, retry and the import and Bookbot next steps
without sending real customer email.
