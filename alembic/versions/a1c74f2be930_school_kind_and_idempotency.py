"""Separate library rows from education units, add idempotency records.

Adds `schools.kind`. Every existing row is an education unit and keeps
`kind = 'school'`; rows created as additional libraries of an organisation use
`kind = 'library'` and may not carry students, classes, home staff or admission
identity. A CHECK covers the columns on `schools`; a shared trigger function
covers the referencing education tables.

Also adds `idempotency_records`, a shared store letting retryable write
operations replay their original response.

Revision ID: a1c74f2be930
Revises: c13ed852fa90
"""

import sqlalchemy as sa
from alembic_utils.pg_function import PGFunction
from alembic_utils.pg_trigger import PGTrigger
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "a1c74f2be930"
down_revision = "c13ed852fa90"
branch_labels = None
depends_on = None

REQUIRE_EDUCATION_UNIT_SCHOOL = PGFunction(
    schema="public",
    signature="require_education_unit_school()",
    definition="""returns trigger LANGUAGE plpgsql
    SET search_path = pg_catalog, public, pg_temp
    AS $function$
    DECLARE
        target_kind text;
    BEGIN
        IF NEW.school_id IS NULL THEN
            RETURN NEW;
        END IF;
        IF TG_ARGV[0] = 'wriveted_identifier' THEN
            SELECT kind INTO target_kind FROM public.schools
            WHERE wriveted_identifier = NEW.school_id;
        ELSE
            SELECT kind INTO target_kind FROM public.schools
            WHERE id = NEW.school_id;
        END IF;
        IF target_kind IS DISTINCT FROM 'school' THEN
            RAISE EXCEPTION
                'Table % cannot reference school % because it is a % row, not an education unit',
                TG_TABLE_NAME, NEW.school_id, target_kind
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $function$""",
)

EDUCATION_UNIT_TRIGGERS = (
    PGTrigger(
        schema="public",
        signature="class_groups_require_education_unit_trigger",
        on_entity="public.class_groups",
        is_constraint=False,
        definition=(
            "BEFORE INSERT OR UPDATE OF school_id ON public.class_groups"
            " FOR EACH ROW EXECUTE FUNCTION"
            " public.require_education_unit_school('wriveted_identifier')"
        ),
    ),
    PGTrigger(
        schema="public",
        signature="students_require_education_unit_trigger",
        on_entity="public.students",
        is_constraint=False,
        definition=(
            "BEFORE INSERT OR UPDATE OF school_id ON public.students"
            " FOR EACH ROW EXECUTE FUNCTION public.require_education_unit_school('id')"
        ),
    ),
    PGTrigger(
        schema="public",
        signature="educators_require_education_unit_trigger",
        on_entity="public.educators",
        is_constraint=False,
        definition=(
            "BEFORE INSERT OR UPDATE OF school_id ON public.educators"
            " FOR EACH ROW EXECUTE FUNCTION public.require_education_unit_school('id')"
        ),
    ),
)


def upgrade() -> None:
    op.add_column(
        "schools",
        sa.Column(
            "kind",
            sa.String(length=20),
            nullable=False,
            server_default="school",
        ),
    )
    op.create_check_constraint("kind", "schools", "kind IN ('school', 'library')")
    op.create_check_constraint(
        "library_kind_has_no_education_identity",
        "schools",
        "kind = 'school' OR (official_identifier IS NULL"
        " AND student_domain IS NULL AND teacher_domain IS NULL)",
    )

    op.create_table(
        "idempotency_records",
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("key", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("response", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name=op.f("fk_idempotency_records_actor_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "operation", "key", name=op.f("pk_idempotency_records")
        ),
    )
    op.create_index(
        op.f("ix_idempotency_records_actor_id"),
        "idempotency_records",
        ["actor_id"],
        unique=False,
    )
    op.create_index(
        "ix_idempotency_records_created_at",
        "idempotency_records",
        ["created_at"],
        unique=False,
    )

    op.create_entity(REQUIRE_EDUCATION_UNIT_SCHOOL)
    for trigger in EDUCATION_UNIT_TRIGGERS:
        op.create_entity(trigger)


def downgrade() -> None:
    connection = op.get_bind()
    if connection.scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM schools WHERE kind <> 'school')")
    ):
        raise RuntimeError(
            "Reassign or remove library-only schools rows before downgrading; "
            "the earlier schema cannot distinguish them from education units"
        )
    for trigger in EDUCATION_UNIT_TRIGGERS:
        op.drop_entity(trigger)
    op.drop_entity(REQUIRE_EDUCATION_UNIT_SCHOOL)
    op.drop_index("ix_idempotency_records_created_at", table_name="idempotency_records")
    op.drop_index(
        op.f("ix_idempotency_records_actor_id"), table_name="idempotency_records"
    )
    op.drop_table("idempotency_records")
    # Bare names: the metadata naming convention expands them to
    # ck_schools_<name>, the same way create_check_constraint did.
    op.drop_constraint(
        "library_kind_has_no_education_identity", "schools", type_="check"
    )
    op.drop_constraint("kind", "schools", type_="check")
    op.drop_column("schools", "kind")
