"""Add pg_trgm GIN index on schools.name for ranked, typo-tolerant search

Enables similarity()/word_similarity() ranking of school-name search so that
queries like "Somer" rank Somerfield highly and minor typos still match,
instead of the previous unranked substring (ILIKE '%q%') filter.

Revision ID: b7e3f1a9c2d5
Revises: d2a7c9b3e1f4
Create Date: 2026-06-19 00:00:00.000000

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "b7e3f1a9c2d5"
down_revision = "d2a7c9b3e1f4"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # GIN trigram index over lower(name) so that both substring (LIKE) recall
    # and word_similarity()/similarity() fuzzy ranking can use the index.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_schools_name_trgm "
        "ON schools USING gin (lower(name) gin_trgm_ops)"
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_schools_name_trgm")
