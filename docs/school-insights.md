# School Insights

Implementation for review; not deployed. Backend and admin worktrees are both `school-insights/`, branch `brian/school-insights`, based on their local main branches. The dependency refresh and older internal KPI worktrees are separate.

## Contract

`GET /v1/school/{wriveted_identifier}/insights?weeks=4` accepts 4, 12 or 26 complete UTC weeks ending on the current Monday. `end_date` is exclusive. The UI keeps this choice in the URL and presents an inclusive human-readable date range. Collection metrics are a current snapshot, not restricted by that period.

Only active WRIVETED/backend administrators and the school's own educators/admins have the dedicated `insights` permission. Students, parents, other schools and LMS credentials cannot use it. View-as GET access is explicitly allowed but evaluated as the target educator; actor staff rights do not carry over. Responses are private/no-store and the frontend's SWR key includes the effective View-as context.

No sessions, identifiers, conversation text or raw feedback objects leave this endpoint. Activity counts below five sessions are withheld. Recommendation and feedback counts also suppress small complementary groups. Weekly breakdowns are withheld together if a week is small; feedback categories are withheld together if any contributing category is small. The session threshold is not a guarantee of five different people or resistance to all repeated-query inference. There are no arbitrary date, class or age filters and no raw exports.

## Definitions and limitations

- Sessions have a nullable `school_id` UUID foreign key, fixed at creation. The server prefers authenticated school membership, otherwise validates the school chat link against existing schools. Historical attribution is backfilled from matching canonical school links, not verified attendance. Anonymous and staff/testing sessions can contribute. Missing attribution is excluded; numeric historical school IDs are not inferred. Later JSON state edits cannot move a session between schools.
- Recommendation reach is a recorded `book_feedback` question milestone. Duplicate history entries count once per session. It is not proof of rendering, reading, completing the carousel or borrowing a book.
- Feedback counts use the latest recorded submission, normalized against ISBNs recorded when the question was offered. Duplicate entries, unknown books and contradictory choices do not count. If any submitted feedback in the cohort cannot be verified (including legacy records), choice totals are withheld rather than reported as zero. Submission counts remain available subject to privacy thresholds. Later submissions may update older cohorts; arbitrary mutable state edits cannot alter recorded choices. Equal history timestamps are deterministically ordered by history UUID, not inferred insertion order.
- Interest groups use recognised `user.hue_keys`, deduplicated per session. At most eight groups with at least five sessions are shown; small non-selecting complements are also suppressed. Collection comparison is hue-labelled distinct works, not guaranteed age/reading-ability fit or available copies.
- Collection coverage counts distinct works across school holdings using the latest labelset by ID. Both hue and reading-ability associations are required. Missing ages, unchecked labels and unmatched catalogue entries are separate overlapping counts. No checked flags or other labels are changed.
- Legacy external-chatbot events and infrastructure latency are not included.
- Completed recommendation journeys and the number of books presented remain deferred: question reach is not renamed as either metric.

## Operations

One aggregate SQL statement gives all dashboard sections the same database snapshot on the request's existing connection. A transaction-local two-second statement timeout bounds database work; timeouts return a retryable 503. JIT is disabled for this transaction only: compilation dominated the short dashboard query in local EXPLAIN measurements. No per-session application fetches or new service. See [PostgreSQL's JIT guidance](https://www.postgresql.org/docs/current/jit-decision.html).

Local synthetic check: 20,000 sessions and 40,000 history records across 26 weeks, without holdings/interests. Request-local JIT off reduced warm sequential elapsed time from about 715–722 ms to 76–77 ms; four concurrent requests sharing two connections completed in 77–189 ms (previously 742–1,561 ms). This is not a Cloud SQL or representative collection benchmark and does not replace the staging release gate.

Migration `e927ad418b62` follows the already-applied staging migration `d813cf906e21`: short DDL statements, restartable 1,000-row backfill batches and a concurrent typed school/date index. Historical JSON is compared to trusted UUID text, never cast. Apply the backend migration before enabling the frontend. The Firebase static export rewrite includes the new `/school/{id}/insights/` route.

Release gate: after old API revisions drain, run `uv run python scripts/backfill_session_schools.py --before <drain-time-with-timezone>` with the deployment's `SQLALCHEMY_DATABASE_URI`. Review its candidate count, repeat with `--apply`, then verify the dry run reports zero before enabling Insights. The old writer does not populate the new column. This restartable catch-up only touches legacy rows; new sessions carry an attribution-version marker, including deliberately unscoped chats. It never infers membership from malformed IDs or present-day membership for historical sessions.

Before release, benchmark the largest staging cohort/collection under concurrent student traffic and complete an authenticated educator walkthrough with the matching backend. The local browser fixtures demonstrate populated, empty, suppressed and denied states; they are synthetic, not school activity measurements.

See [analytics-proposal.md](analytics-proposal.md) for the separate daily-aggregate/DuckDB proposal. It is not an infrastructure change in this feature.

## Local validation

- Full backend unit suite: 661 passed.
- Docker-backed chat/insights regression suite: 40 passed (including actual PostgreSQL queries, authenticated HTTP contract, typed attribution and feedback recording).
- Insights + existing View-as/profile browser suite: 29 passed at desktop, portrait and landscape sizes.
- TypeScript typecheck, targeted Ruff checks, whitespace checks and the admin production static export passed. Existing unrelated lint warnings remain.
- The typed-attribution migration passed upgrade, downgrade, re-upgrade and retry after a simulated unstamped application on a separate local database. Valid/uppercase links matched; malformed/unknown links stayed null. The post-drain catch-up dry-run/apply/recheck only updated the simulated old writer, not a new unscoped session. The typed index and single-statement snapshot were inspected.

Browser screenshots live in the admin worktree's `test-results/school-insights-{1440,390,844}.png`; rerunning Playwright replaces disposable test results.
