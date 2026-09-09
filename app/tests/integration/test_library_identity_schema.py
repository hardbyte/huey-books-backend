from uuid import uuid4

import pytest
from sqlalchemy import MetaData, insert, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateIndex

from alembic.autogenerate import produce_migrations, render_python_code
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.models.library import EducationUnit, EducationUnitLibrary, Library
from app.models.organisation import Organisation
from app.models.school import School


@pytest.fixture
def identity_connection(session):
    connection = session.connection()
    transaction = connection.begin_nested()
    try:
        yield connection
    finally:
        transaction.rollback()


def seed_identities(connection):
    organisation_id, other_org, library_id, education_id = (uuid4() for _ in range(4))
    connection.execute(
        insert(Organisation),
        [
            {"id": organisation_id, "name": "Schema test", "kind": "school"},
            {"id": other_org, "name": "Other tenant", "kind": "public_library"},
        ],
    )
    connection.execute(
        insert(Library).values(
            id=library_id, organisation_id=organisation_id, name="Shared library"
        )
    )
    connection.execute(
        insert(EducationUnit).values(
            id=education_id, organisation_id=organisation_id, name="School"
        )
    )
    return organisation_id, other_org, library_id, education_id


def test_school_search_index_declaration_matches_existing_schema(identity_connection):
    index = next(
        index
        for index in School.__table__.indexes
        if index.name == "idx_schools_name_trgm"
    )
    declared = str(CreateIndex(index).compile(dialect=postgresql.dialect()))
    assert "USING gin (lower(name) gin_trgm_ops)" in declared
    deployed = identity_connection.scalar(
        text("SELECT pg_get_indexdef('idx_schools_name_trgm'::regclass)")
    )
    assert "USING gin (lower((name)::text) gin_trgm_ops)" in deployed


def test_new_identity_defaults_use_uuidv7(identity_connection):
    connection = identity_connection
    organisation_id = connection.execute(
        insert(Organisation)
        .values(name="UUID defaults", kind="school")
        .returning(Organisation.id)
    ).scalar_one()
    assert organisation_id.version == 7
    for model in (Library, EducationUnit):
        identifier = connection.execute(
            insert(model)
            .values(organisation_id=organisation_id, name="New identity")
            .returning(model.id)
        ).scalar_one()
        assert identifier.version == 7
    raw_identifier = connection.execute(
        text(
            "INSERT INTO organisations (name, kind) VALUES ('Raw SQL', 'school') RETURNING id"
        )
    ).scalar_one()
    assert raw_identifier.version == 7


def test_uuidv7_migration_is_generated_from_declarative_defaults(identity_connection):
    connection = identity_connection
    metadata = MetaData()
    for model in (Organisation, Library, EducationUnit):
        model.__table__.to_metadata(metadata)
    context = MigrationContext.configure(
        connection,
        opts={
            "compare_server_default": True,
            "include_schemas": False,
            "include_name": lambda name, kind, parents: (
                kind != "table" or name in metadata.tables
            ),
        },
    )
    operations = Operations(context)
    for table_name in metadata.tables:
        operations.alter_column(table_name, "id", server_default=None)
    migration = produce_migrations(context, metadata)
    rendered = render_python_code(migration.upgrade_ops)
    assert rendered.count("op.alter_column(") == 3
    assert rendered.count("uuidv7()") == 3
    assert "from app" not in rendered
    for table_operations in migration.upgrade_ops.ops:
        for operation in table_operations.ops:
            operations.invoke(operation)
    assert produce_migrations(context, metadata).upgrade_ops.is_empty()


def test_shared_library_can_serve_two_education_units(identity_connection):
    connection = identity_connection
    organisation_id, _, library_id, education_id = seed_identities(connection)
    other_education = uuid4()
    connection.execute(
        insert(EducationUnit).values(
            id=other_education, organisation_id=organisation_id, name="Senior school"
        )
    )
    for unit in (education_id, other_education):
        connection.execute(
            insert(EducationUnitLibrary).values(
                organisation_id=organisation_id,
                library_id=library_id,
                education_unit_id=unit,
            )
        )
    assert (
        len(
            connection.execute(
                select(EducationUnitLibrary).where(
                    EducationUnitLibrary.library_id == library_id
                )
            ).all()
        )
        == 2
    )


def test_cross_organisation_association_is_rejected(identity_connection):
    connection = identity_connection
    organisation_id, other_org, library_id, education_id = seed_identities(connection)
    connection.execute(
        EducationUnit.__table__.update()
        .where(EducationUnit.id == education_id)
        .values(organisation_id=other_org)
    )
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(
            insert(EducationUnitLibrary).values(
                organisation_id=organisation_id,
                library_id=library_id,
                education_unit_id=education_id,
            )
        )


def test_organisation_delete_cannot_cascade_into_libraries(identity_connection):
    connection = identity_connection
    organisation_id, _, _, _ = seed_identities(connection)
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(
            Organisation.__table__.delete().where(Organisation.id == organisation_id)
        )


def test_linked_library_cannot_be_reassigned_or_deleted(identity_connection):
    connection = identity_connection
    organisation_id, other_org, library_id, education_id = seed_identities(connection)
    connection.execute(
        insert(EducationUnitLibrary).values(
            organisation_id=organisation_id,
            library_id=library_id,
            education_unit_id=education_id,
        )
    )
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(
            Library.__table__.update()
            .where(Library.id == library_id)
            .values(organisation_id=other_org)
        )
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(Library.__table__.delete().where(Library.id == library_id))
