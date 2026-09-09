import importlib.util
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.db.functions import collection_legacy_default
from app.db.triggers import collection_legacy_default_trigger
from app.models.collection import Collection


def legacy_insert(session, school_uuid, name="Old writer"):
    return session.execute(
        text("""
        INSERT INTO collections (name, school_id) VALUES (:name, :school)
        RETURNING id, is_default
    """),
        {"name": name, "school": school_uuid},
    ).one()


def test_old_insert_and_replacement_retain_default(session, test_school):
    first = legacy_insert(session, test_school.school_uuid)
    assert first.is_default is True
    session.execute(text("DELETE FROM collections WHERE id = :id"), {"id": first.id})
    replacement = legacy_insert(session, test_school.school_uuid)
    assert replacement.is_default is True
    assert replacement.id != first.id


def test_old_personal_insert_is_not_default(session):
    assert legacy_insert(session, None).is_default is False


def test_new_explicit_additional_collection_is_preserved(session, test_school):
    first = legacy_insert(session, test_school.school_uuid)
    additional = Collection(
        name="New writer", school_id=test_school.school_uuid, is_default=False
    )
    session.add(additional)
    session.flush()
    assert additional.is_default is False
    assert (
        session.scalar(
            text("SELECT is_default FROM collections WHERE id = :id"), {"id": first.id}
        )
        is True
    )
    with pytest.raises(IntegrityError, match="Legacy collection writer"):
        with session.begin_nested():
            legacy_insert(session, test_school.school_uuid)


def rolling_revision():
    path = (
        Path(__file__).resolve().parents[3]
        / "alembic/versions/0a6db7f4e561_collection_rolling_compatibility.py"
    )
    spec = importlib.util.spec_from_file_location("rolling_revision", path)
    revision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(revision)
    return revision


def test_migration_snapshots_match_declarative_entities():
    revision = rolling_revision()
    for declared, snapshot in [
        (collection_legacy_default, revision.public_collection_legacy_default),
        (
            collection_legacy_default_trigger,
            revision.public_collections_collection_legacy_default_trigger,
        ),
    ]:
        assert declared.identity == snapshot.identity
        assert declared.definition == snapshot.definition


def test_concurrent_legacy_inserts_cannot_create_two_inventories(session, test_school):
    school_uuid = test_school.school_uuid
    barrier = Barrier(2)
    engine = create_engine(session.get_bind().url, poolclass=NullPool)

    def insert():
        with engine.connect() as connection:
            connection.execute(text("SET LOCAL lock_timeout = '5s'"))
            barrier.wait(timeout=5)
            try:
                result = connection.execute(
                    text("""
                    INSERT INTO collections (name, school_id) VALUES ('Concurrent legacy', :school)
                    RETURNING is_default
                """),
                    {"school": school_uuid},
                ).scalar_one()
                connection.commit()
                return result
            except IntegrityError:
                connection.rollback()
                return False

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: insert(), range(2)))
    finally:
        engine.dispose()
    assert sorted(results) == [False, True]
    assert (
        session.scalar(
            text("SELECT count(*) FROM collections WHERE school_id = :school"),
            {"school": school_uuid},
        )
        == 1
    )


def test_upgrade_catches_sole_legacy_collection_and_is_reversible(session, test_school):
    revision = rolling_revision()
    connection = session.connection()
    with connection.begin_nested() as savepoint:
        revision.op = Operations(MigrationContext.configure(connection))
        revision.downgrade()
        inserted = legacy_insert(session, test_school.school_uuid)
        assert inserted.is_default is False
        revision.upgrade()
        assert (
            session.scalar(
                text("SELECT is_default FROM collections WHERE id = :id"),
                {"id": inserted.id},
            )
            is True
        )
        revision.downgrade()
        revision.upgrade()
        assert (
            session.scalar(
                text("SELECT is_default FROM collections WHERE id = :id"),
                {"id": inserted.id},
            )
            is True
        )
        savepoint.rollback()


def test_upgrade_refuses_ambiguous_missing_defaults(session, test_school):
    revision = rolling_revision()
    connection = session.connection()
    with connection.begin_nested() as savepoint:
        revision.op = Operations(MigrationContext.configure(connection))
        revision.downgrade()
        legacy_insert(session, test_school.school_uuid, "One")
        legacy_insert(session, test_school.school_uuid, "Two")
        with pytest.raises(RuntimeError, match="multiple collections and no default"):
            revision.upgrade()
        savepoint.rollback()
