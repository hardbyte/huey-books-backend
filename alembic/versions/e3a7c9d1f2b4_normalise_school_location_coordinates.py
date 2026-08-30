"""Normalise numeric school location coordinates to strings

School.info is untyped JSONB and different write paths have stored the
location block's lat/long coordinates inconsistently: some as JSON numbers,
some as strings. The response schema serialises coordinates as strings, so a
numeric value would otherwise break serialisation. This rewrites any numeric
lat/long stored under info->'location' to its string form in place, leaving
every other info key untouched.

Revision ID: e3a7c9d1f2b4
Revises: d2f3a4b5c6e7
Create Date: 2026-08-30

"""

from alembic import op

revision = "e3a7c9d1f2b4"
down_revision = "d2f3a4b5c6e7"
branch_labels = None
depends_on = None


def upgrade():
    # Only touch rows where a coordinate is currently a JSON number. jsonb_set
    # rewrites the value in place (to_jsonb of the text form yields a JSON
    # string), so all other info keys are preserved. The two coordinates are
    # handled independently so a row with only one numeric coordinate is still
    # fully normalised.
    op.execute(
        """
        UPDATE schools
        SET info = jsonb_set(
            info,
            '{location,lat}',
            to_jsonb((info->'location'->>'lat'))
        )
        WHERE jsonb_typeof(info->'location'->'lat') = 'number'
        """
    )
    op.execute(
        """
        UPDATE schools
        SET info = jsonb_set(
            info,
            '{location,long}',
            to_jsonb((info->'location'->>'long'))
        )
        WHERE jsonb_typeof(info->'location'->'long') = 'number'
        """
    )


def downgrade():
    # No-op: coercing coordinates to strings is a lossless normalisation and
    # the string form is the canonical shape, so there is nothing to revert.
    pass
