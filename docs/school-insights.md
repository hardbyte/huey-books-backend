# School Insights

Identifier conventions and compatibility policy: [Neutral identifier naming](identifier-naming.md).

This document defines the dashboard contract and operational checks. Deployment
status belongs in release records, not this specification.

## Contract

`GET /v1/school/{school_uuid}/insights?weeks=4` accepts 4, 12 or 26 complete UTC weeks ending on the current Monday. `end_date` is exclusive. The UI keeps this choice in the URL and presents an inclusive human-readable date range. Collection metrics are a current snapshot, not restricted by that period. The response uses `school_uuid`; the former UUID-valued `school_id` is a deprecated alias during client migration. The URL itself is unchanged.

Only active WRIVETED/backend administrators and the school's own educators/admins have the dedicated `insights` permission. Students, parents, other schools and LMS credentials cannot use it. View-as GET access is explicitly allowed but evaluated as the target educator; actor staff rights do not carry over. Responses are private/no-store and the frontend's SWR key includes the effective View-as context.

No sessions, identifiers, conversation text or raw feedback objects leave this endpoint. Activity counts below five sessions are withheld. Recommendation and feedback counts also suppress small complementary groups. Weekly breakdowns are withheld together if a week is small; feedback categories are withheld together if any contributing category is small. The session threshold is not a guarantee of five different people or resistance to all repeated-query inference. There are no arbitrary date, class or age filters and no raw exports.

## Definitions and limitations

`semantics` makes the UTC boundary, exclusive end date, session-start cohort, latest outcomes, current collection snapshot and ISBN-choice unit explicit. This is not a count of all events occurring during the period: a session started before it is excluded even if it submits feedback during it. The supported `weeks` values are enumerated in OpenAPI.

`availability` accompanies the existing numeric fields. `available` includes genuine zero counts; `privacy_suppressed` accompanies withheld values; `no_sessions` explains an undefined recommendation rate; `unverified_history` explains withheld feedback choices. Privacy suppression takes precedence over historical-data explanations. Trends are either available (including zero weeks) or suppressed as a whole. Interests are always `privacy_filtered`: an empty list deliberately does not reveal whether a small group exists. Older clients may ignore these additive fields.

- Sessions have a nullable `school_id` UUID foreign key, fixed at creation. The server prefers authenticated school membership, otherwise validates the school chat link against existing schools. Historical attribution is backfilled from matching canonical school links, not verified attendance. Anonymous and staff/testing sessions can contribute. Missing attribution is excluded; numeric historical school IDs are not inferred. Later JSON state edits cannot move a session between schools.
- Recommendation reach is a recorded `book_feedback` question milestone. Duplicate history entries count once per session. It is not proof of rendering, reading, completing the carousel or borrowing a book.
- Feedback counts use the latest recorded submission, normalized against ISBNs recorded when the question was offered. Duplicate entries, unknown books and contradictory choices do not count. If any submitted feedback in the cohort cannot be verified (including legacy records), choice totals are withheld rather than reported as zero. Submission counts remain available subject to privacy thresholds. Later submissions may update older cohorts; arbitrary mutable state edits cannot alter recorded choices. Equal history timestamps are deterministically ordered by history UUID, not inferred insertion order.
- Interest groups use recognised `user.hue_keys`, deduplicated per session. At most eight groups with at least five sessions are shown; small non-selecting complements are also suppressed. Collection comparison is hue-labelled distinct works, not guaranteed age/reading-ability fit or available copies.
- Collection coverage counts distinct works across school holdings using the latest labelset by ID. Both hue and reading-ability associations are required. Missing ages, unchecked labels and unmatched catalogue entries are separate overlapping counts. No checked flags or other labels are changed.
- Legacy external-chatbot events and infrastructure latency are not included.
- Completed recommendation journeys and the number of books presented remain deferred: question reach is not renamed as either metric.

## Operations

One aggregate SQL statement gives all dashboard sections the same database snapshot on the request's existing connection. A transaction-local two-second statement timeout bounds database work; timeouts return a retryable 503. JIT is disabled for this transaction only: compilation dominated the short dashboard query in local EXPLAIN measurements. No per-session application fetches or new service. See [PostgreSQL's JIT guidance](https://www.postgresql.org/docs/current/jit-decision.html).

Benchmark with representative session history, holdings and interests, including
concurrent student traffic. Record query plans, cohort sizes, pool limits and
warm/cold timings with the release evidence. A synthetic local timing is not a
Cloud SQL performance guarantee.

Migration `e927ad418b62` directly follows `a1c2e3f40014`: short DDL statements, restartable 1,000-row backfill batches and a concurrent typed school/date index. Historical JSON is compared to trusted UUID text, never cast. Apply the backend migration before enabling the frontend. The Firebase static export must route `/school/{id}/insights/` correctly.

Historical development-database recovery: the intermediate revision
`d813cf906e21` was removed when the preproduction migration chain was collapsed.
A database at that revision must finish upgrading with the old chain before
using the replacement chain; do not blindly stamp it. Databases already at
`e927ad418b62` need no stamp or rollback for that transition. Downgrading that
migration restores the schema at `a1c2e3f40014`, without the discarded
JSON-expression index. This is a compatibility note, not permission to rewrite
other executed migrations.

Release gate: after old API revisions drain, run `uv run python scripts/backfill_session_schools.py --before <drain-time-with-timezone>` with the deployment's `SQLALCHEMY_DATABASE_URI`. Review its candidate count, repeat with `--apply`, then verify the dry run reports zero before enabling Insights. The old writer does not populate the new column. This restartable catch-up only touches legacy rows; new sessions carry an attribution-version marker, including deliberately unscoped chats. It never infers membership from malformed IDs or present-day membership for historical sessions.

Before release, benchmark the largest staging cohort/collection under concurrent student traffic and complete an authenticated educator walkthrough with the matching backend. The local browser fixtures demonstrate populated, empty, suppressed and denied states; they are synthetic, not school activity measurements.

See [analytics.md](analytics.md) for application-wide metric
definitions and the separately gated daily-aggregate/DuckDB design, and
[observability-architecture.md](observability-architecture.md) for OTel signals and
Google Cloud operations. Neither changes this endpoint's cohort/privacy contract.

## Regression checks

- `app/tests/unit/test_school_insights.py`: UTC week boundaries, availability precedence and identifier compatibility.
- `app/tests/integration/test_school_insights.py`: PostgreSQL aggregates, privacy suppression, authorization and late feedback remaining in its original session-start cohort. Include View-as regressions when access policy changes.
- Admin browser checks: populated, empty, suppressed, denied, unverified-feedback and legacy-response states at desktop, portrait and landscape sizes. Check URL persistence and the production static export as well as the development server.
- Migration checks on disposable databases: fresh replay, upgrade of populated data, downgrade/re-upgrade and retry after an unstamped application. Valid links should match; malformed or unknown links must remain unattributed.
- Catch-up checks: dry-run/apply/recheck must update old-writer rows without changing deliberately unscoped new sessions. Inspect the typed index and validated foreign key after migration.

Keep screenshots and measured test results with the change under review. They
are evidence for that revision, not a substitute for repeating these checks.
