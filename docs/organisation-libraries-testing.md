# Organisation/library workspace testing

Verify the workspace in isolated development and staging. Production activation follows the gates in [the rollout procedure](organisation-library-rollout.md).

## Fixtures and authentication

Migrate an empty dedicated PostgreSQL database using `uv run alembic upgrade head`, then run the existing `scripts/seed_admin_ui_data.py` followed by `scripts/seed_organisation_demo.py` with `uv run python`. The organisation seed refuses databases other than `organisation_prototype` or numbered PR databases on the development Cloud SQL instance. Never run either seed against production.

The synthetic fixtures contain a primary/middle and high-school library, a three-branch public library, and an unrelated library. `central-manager@organisation-demo.example.org` manages both organisations; `high-librarian@organisation-demo.example.org` manages only the high-school library; `reviewer@organisation-demo.example.org` has review access; `outsider@organisation-demo.example.org` has only its unrelated home library. The reserved example domain accepts normal email validation without referring to a real school's accounts.

For unattended browser testing, mint short-lived ordinary user tokens with `app.services.security.create_access_token` for those database users. Supply tokens to an isolated browser context in memory, never logs, screenshots, source files, URLs or PR descriptions. No authentication bypass endpoint is needed. The admin preview must point explicitly to the companion PR API, not development-main or production.

## Checks

- `uv run ruff check .` and `uv run ruff format --check .`.
- Run the repository Docker integration harness against a fresh isolated database. Include the current `app`, `scripts` and `alembic` trees when reusing a cached image; otherwise migration tests can accidentally exercise old files.
- Exercise `test_organisation_workspace.py`, `test_multiple_collections.py`, legacy collection updates and MCP operations, chat context, and materialized-view recommendation tests.
- Verify both an empty-database migration chain and an upgrade from the previous populated schema. Downgrade must refuse to discard organisation, membership or additional-collection data.
- In the admin companion branch: TypeScript, lint, build and `e2e/libraries-workspace.spec.ts`. Do not run Next development and production builds concurrently in the same worktree.

## Browser acceptance

1. As central manager, switch between organisations and create a library with a named country and default collection.
2. Select an exact library and create another collection. Preview a CSV/XLSX, check the destination, confirm, and verify the default and sibling inventories are unchanged.
3. Grant and remove an existing synthetic staff account's access. Check the same token loses the removed grant while genuine home-library permissions remain.
4. Verify a reviewer cannot import or manage access; a local manager cannot reach siblings; View As is read-only.
5. As platform staff, explicitly attach an independent synthetic library. Confirm this does not activate readers or change billing/student ownership.
6. Inspect portrait, landscape and desktop layouts after transitions settle. Capture only synthetic data.
7. Combine directory name, organisation, country and catalogue-presence filters; verify totals and every page use the same filters, and restricted users cannot discover other libraries through filtering.

Directory `country_code` is an uppercase alpha-3 code. Optional `has_catalogue`
means an institutional collection exists, including an empty catalogue; it does
not mean books are present, the school is active or its subscription is paid.
Both filters are applied within the existing access scope before counting and
pagination. Omitting them preserves the unfiltered directory.

The prototype import is additive and bounded to 1,000 rows. It does not remove absent books, hydrate shared bibliographic metadata, activate subscriptions, or move students. Invitation delivery and organisation billing are separate design decisions.
