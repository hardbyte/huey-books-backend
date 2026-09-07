# School Insights

Implementation for review; not deployed. Backend and admin worktrees are both `school-insights/`, branch `brian/school-insights`, based on their local main branches. The dependency refresh and older internal KPI worktrees are separate.

## Contract

`GET /v1/school/{wriveted_identifier}/insights?weeks=4` accepts 4, 12 or 26 complete UTC weeks ending on the current Monday. `end_date` is exclusive. The UI keeps this choice in the URL and presents an inclusive human-readable date range. Collection metrics are a current snapshot, not restricted by that period.

Only active WRIVETED/backend administrators and the school's own educators/admins have the dedicated `insights` permission. Students, parents, other schools and LMS credentials cannot use it. View-as GET access is explicitly allowed but evaluated as the target educator; actor staff rights do not carry over. Responses are private/no-store and the frontend's SWR key includes the effective View-as context.

No sessions, identifiers, conversation text or raw feedback objects leave this endpoint. Activity counts below five sessions are withheld. Recommendation and feedback counts also suppress small complementary groups. Weekly breakdowns are withheld together if a week is small; feedback categories are withheld together if any contributing category is small. The session threshold is not a guarantee of five different people or resistance to all repeated-query inference. There are no arbitrary date, class or age filters and no raw exports.

## Definitions and limitations

- Sessions are attributed by `context.school_wriveted_id`, the school chat link—not verified attendance. Anonymous and staff/testing sessions can contribute. Missing attribution is excluded; numeric historical school IDs are not inferred.
- Recommendation reach is a recorded `book_feedback` question milestone. Duplicate history entries count once per session. It is not proof of rendering, reading, completing the carousel or borrowing a book.
- Feedback counts use the latest `temp.book_feedback` choices for sessions with a recorded feedback submission. Mutable state means later activity may change older cohorts. Missing/malformed arrays contribute no recorded choices, not an inferred negative response.
- Interest groups use recognised `user.hue_keys`, deduplicated per session. At most eight groups with at least five sessions are shown. Collection comparison is hue-labelled distinct works, not guaranteed age/reading-ability fit or available copies.
- Collection coverage counts distinct works across school holdings using the latest labelset by ID. Both hue and reading-ability associations are required. Missing ages, unchecked labels and unmatched catalogue entries are separate overlapping counts. No checked flags or other labels are changed.
- Legacy external-chatbot events and infrastructure latency are not included.

## Operations

Three bounded aggregate queries, no per-session application fetches, no new service. Migration `d813cf906e21` adds a concurrent school/date index; its partial predicate excludes malformed/unbounded context strings. Apply the backend migration before enabling the frontend. The Firebase static export rewrite includes the new `/school/{id}/insights/` route.

Before release, benchmark the largest staging cohort/collection under concurrent student traffic and complete an authenticated educator walkthrough with the matching backend. The local browser fixtures demonstrate populated, empty, suppressed and denied states; they are synthetic, not school activity measurements.

See [analytics-proposal.md](analytics-proposal.md) for the separate daily-aggregate/DuckDB proposal. It is not an infrastructure change in this feature.

## Local validation

- Full backend unit suite: 647 passed.
- Docker-backed school-insights suite: 27 passed (including actual PostgreSQL queries and authenticated HTTP contract).
- Insights + existing View-as/profile browser suite: 29 passed at desktop, portrait and landscape sizes.
- TypeScript typecheck, targeted Ruff checks, whitespace checks and the admin production static export passed. Existing unrelated lint warnings remain.
- The concurrent index migration was downgraded and upgraded successfully on the dedicated local PostgreSQL test database; its definition was inspected.

Browser screenshots live in the admin worktree's `test-results/school-insights-{1440,390,844}.png`; rerunning Playwright replaces disposable test results.
