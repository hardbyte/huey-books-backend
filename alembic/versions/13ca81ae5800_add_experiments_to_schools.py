"""Add experiments to schools

Revision ID: 13ca81ae5800
Revises: ad2bcbab60ae
Create Date: 2022-03-24 13:54:33.245721

"""

# revision identifiers, used by Alembic.
from sqlalchemy import JSON, Integer, column, select, table

from alembic import op

revision = "13ca81ae5800"
down_revision = "ad2bcbab60ae"
branch_labels = None
depends_on = None

schools = table("schools", column("id", Integer), column("info", JSON))


def upgrade():
    bind = op.get_bind()
    for school in bind.execute(select(schools.c.id, schools.c.info)).all():
        info = (
            dict(school.info)
            if school.info is not None
            else {"location": {"suburb": None, "state": "Unknown", "postcode": ""}}
        )
        info["experiments"] = {"no-jokes": False, "no-choice-option": True}
        bind.execute(
            schools.update().where(schools.c.id == school.id).values(info=info)
        )


def downgrade():
    bind = op.get_bind()
    for school in bind.execute(select(schools.c.id, schools.c.info)).all():
        info = dict(school.info or {})
        if "experiments" in info:
            del info["experiments"]
            bind.execute(
                schools.update().where(schools.c.id == school.id).values(info=info)
            )
