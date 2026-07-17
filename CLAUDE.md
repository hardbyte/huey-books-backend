# CLAUDE.md

## Engineering Principles

- **Test-Design-Code Alignment**: When tests fail, analyze the **design intent** (documentation, API contracts), **current implementation**, and **test expectations** to determine the correct path forward. Don't take the easy path of changing tests to match broken implementations. Instead, compare all three sources to determine what the correct behavior should be and fix accordingly.
- **REST API Consistency**: DELETE operations should return appropriate HTTP status codes per REST conventions (typically 204 No Content for successful deletions with no response body, or 200 OK if returning meaningful response data).
- **Declarative Database Infrastructure**: Use `alembic_utils` with Python-defined functions and triggers in `app/db/functions.py` and `app/db/triggers.py` for version-controlled, type-safe database logic.

## Code Style

Only professional comments should be used:
- Remove task-focused comments like "OLD import removed"
- Remove "NEW:" markers etc.
- Focus on why not what
- Remove comments that just restate the code

Ruff for linting (configured in `pyproject.toml`).

## Development Commands

See [README.md](README.md) for full setup, testing, migration, and deployment instructions.

Quick reference:

Dependencies are managed with **uv** (`pyproject.toml` + `uv.lock`).

| Task | Command |
|------|---------|
| Install dependencies | `uv sync` |
| Start stack | `docker compose up -d --build` |
| Apply migrations | `docker compose run --rm migration` |
| Seed data | `docker compose run --rm --entrypoint python -v "$PWD/scripts:/app/scripts" api /app/scripts/seed_admin_ui_data.py --emit-tokens --tokens-format json` |
| Unit tests | `uv run pytest app/tests/unit/ -v` |
| Integration tests | `bash scripts/integration-tests.sh` |
| Single test | `uv run pytest -v app/tests/integration/test_specific.py::test_function` |
| Lint | `uv run ruff check` |
| Lint fix | `uv run ruff check --fix` |
| Add a dependency | `uv add <pkg>` (`--dev` for dev group) |
| Run API directly | `uv run uvicorn app.main:app --reload` |
| Run internal API | `uv run uvicorn app.internal_api:internal_app --port 8888` |

**Important**: Integration tests should be run using `bash scripts/integration-tests.sh` which provides the proper Docker environment. Running integration tests directly with pytest may encounter async fixture issues. Ensure no conflicting postgres containers are running on port 5432.

### Configuring Local User Permissions

To grant admin access for testing the CMS/chatflow builder in the admin UI:

```sql
UPDATE users SET type = 'WRIVETED' WHERE email = 'your-email@example.com';

INSERT INTO wriveted_admins (id)
SELECT id FROM users WHERE email = 'your-email@example.com';
```

After updating, log out and back in to get a new JWT with updated permissions.

## Architecture Overview

### Dual API Structure
- **Public API** (`app.main:app`): External-facing REST API with authentication/authorization
- **Internal API** (`app.internal_api:internal_app`): Background task processing, webhook handling

### Database
- **ORM**: SQLAlchemy 2.0 with async support (asyncpg driver)
- **Migrations**: Alembic for schema management
- **Base Class**: Custom `Base` class with auto-generated table names
- **User Model**: Uses joined-table inheritance for different user types (Student, Educator, Parent, etc.)
- **Connection**: Always use `SQLALCHEMY_DATABASE_URI` environment variable

### Code Organization
- **Routes**: `app/api/` with dependencies in `app/api/dependencies/`
- **Schemas**: Pydantic request/response models in `app/schemas/`
- **Repositories**: Domain-focused data access in `app/repositories/` (modern pattern)
- **CRUD**: Legacy data access in `app/crud/` (being phased out)
- **Services**: Business logic in `app/services/`
- **Configuration**: Pydantic-based settings in `app/config.py`

See [docs/architecture-service-layer.md](docs/architecture-service-layer.md) for the full service layer architecture.

### Migration Workflow
1. Modify SQLAlchemy models in `app/models/`
2. Add imports to `app/models/__init__.py`
3. Generate migration: `uv run alembic revision --autogenerate -m "Description"`
4. Review generated migration file manually. Models are source of truth.
5. Apply: `uv run alembic upgrade head`

## Common Patterns and Pitfalls

### Data Access
- **Repository pattern** is the modern approach; `app/crud/` is legacy (being phased out)
- All new services should use proper async/await patterns
- Pydantic schemas use consistent field names between database and API

### API Endpoints
- Many endpoints require service account or admin authentication
- Custom validation endpoints like `/flows/{id}/validate` for business logic
- List endpoints support filtering, searching, and pagination consistently

### Testing
- Always use `bash scripts/integration-tests.sh` for integration tests (proper Docker environment)
- Clean up test data in fixtures to prevent test interference
- Be careful with SQLAlchemy async session management in tests
- Test configuration lives in `conftest.py` files

### Performance
- Bulk operations: use batch create/update for efficiency
- Full-text search uses PostgreSQL tsvector and GIN indexes
- Recommendations use the `recommendable_editions` materialized view (see below)

### Recommendation Engine (`recommendable_editions` MV)

The recommendation system is backed by a materialized view pre-computing one row
per work (latest labelset, best cover edition, hue/reading-ability key arrays).

**Key files**:
- `app/db/views.py` — `PGMaterializedView` definition
- `app/db/functions.py` — `refresh_recommendable_editions_function` (non-blocking CONCURRENTLY refresh)
- `app/services/recommendations.py` — `get_recommended_editions_from_mv` (scored query)
- `app/api/recommendations.py` — single-pass scored API, replacing the old 4-level fallback

**Scoring** (higher wins): school-collection match (4), reading-ability overlap (2), hue overlap (1).
Results ordered by score DESC then random() within each tier.

**Refresh**:
- Weekly via Cloud Scheduler → `POST /maintenance/refresh-recommendations` (internal API)
- Debounced on label writes (`PATCH /labelsets`, `PATCH /work/{id}` label edits,
  and promoted reviews) via Cloud Tasks named task `refresh-recommendable-editions`
  (deduplicates within ~4-hour GCP window; fires ~60 s after the last write)

After adding or modifying labelsets in bulk, force a refresh locally with:
```sql
REFRESH MATERIALIZED VIEW CONCURRENTLY recommendable_editions;
```

### REST Conventions
- Verify actual API behavior vs REST conventions for status codes
- Ensure async/await consistency across all database operations
