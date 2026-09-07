# Neutral identifier naming

Use `school_uuid` for a school's public UUID and `school_id` for its internal integer identifier. In TypeScript, use `schoolUuid` and `schoolId` respectively. UUIDs identify resources; authorization still requires explicit permission checks.

Debrand incrementally when touching an interface, not through a repository-wide replacement:

- New interfaces use neutral, unambiguous names. Existing `wriveted_identifier` and UUID-valued `school_id` contracts remain compatibility interfaces until their consumers migrate.
- Map legacy names at adapters or model aliases; keep the rest of new code neutral. Do not introduce a second independent identifier value.
- Mark old response aliases deprecated in OpenAPI. Update first-party consumers, document the transition, and remove aliases only through an explicit compatibility decision.
- A route placeholder can change without changing its URL, but update route-based permission lists and OpenAPI clients together.
- Physical columns require planned migrations with dependency, backfill, rollback and rolling-deployment checks. Do not rename deployed infrastructure, auth role values, secrets, historical migrations or external school identifiers as incidental cleanup.

The first application is School Insights: `school_uuid` is canonical, its previous UUID-valued `school_id` response field is a deprecated compatibility alias, and the UI adapter accepts both generations. `School.school_uuid` aliases the existing database column. Existing school APIs, chat context keys and physical FK column names remain unchanged; migrate those at their own compatibility seams.

Next steps: migrate shared school serializers/clients together, then plan physical-column renames after auditing integer-versus-UUID school references. Keep the broader schema audit separate from naming-only edits.
