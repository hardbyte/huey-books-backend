from datetime import date

import pytest
from sqlalchemy import select

from app import crud
from app.models import PublicReader, SchoolAdmin, Student, WrivetedAdmin
from app.models.reader import Reader
from app.models.user import UserAccountType
from app.schemas.users.user_create import UserCreateIn
from app.schemas.users.user_update import InternalUserUpdateIn, UserUpdateIn
from app.tests.util.random_strings import random_lower_string


def test_user_crud_extracts_name_components(session, test_school):
    publicuser = crud.user.create(
        db=session,
        obj_in=UserCreateIn(
            name="Test User",
            email=f"{random_lower_string(6)}@test.com",
            # first_name="Test",
            # last_name_initial="U",
        ),
        commit=False,
    )
    assert isinstance(publicuser, PublicReader), (
        "CRUD: User account without specified type not constructing a PublicReader object"
    )

    assert publicuser.first_name == "Test"
    assert publicuser.last_name_initial == "U"


def test_user_crud_types(session, test_school, test_class_group):
    test_email = f"{random_lower_string(6)}@test.com"
    publicuser = crud.user.create(
        db=session,
        obj_in=UserCreateIn(
            name="integration test account (user)",
            email=test_email,
            first_name="Test",
            last_name_initial="T",
        ),
        commit=True,
    )
    assert isinstance(publicuser, PublicReader), (
        "CRUD: User account without specified type not constructing a PublicReader object"
    )
    retrieved_public_user = crud.user.get_by_account_email(session, test_email)
    assert retrieved_public_user is not None, "couldn't retrieve public user by email"
    assert retrieved_public_user.id == publicuser.id
    deleted_public_user = crud.user.remove(session, id=publicuser.id)
    assert deleted_public_user.type == UserAccountType.PUBLIC

    wrivetedadmin = crud.user.create(
        db=session,
        obj_in=UserCreateIn(
            name="integration test account (wriveted admin)",
            email=f"{random_lower_string(6)}@test.com",
            type=UserAccountType.WRIVETED,
        ),
        commit=False,
    )
    assert isinstance(wrivetedadmin, WrivetedAdmin), (
        "CRUD: User account with type='wriveted' not constructing a WrivetedAdmin object"
    )
    crud.user.remove(session, id=wrivetedadmin.id)

    schooladmin = crud.user.create(
        db=session,
        obj_in=UserCreateIn(
            name="integration test account (school admin)",
            email=f"{random_lower_string(6)}@test.com",
            type=UserAccountType.SCHOOL_ADMIN,
            school_id=test_school.id,
        ),
        commit=False,
    )
    assert isinstance(schooladmin, SchoolAdmin), (
        "CRUD: User account with type='SCHOOL_ADMIN' not constructing a SchoolAdmin object"
    )
    crud.user.remove(session, id=schooladmin.id)
    student = crud.user.create(
        db=session,
        obj_in=UserCreateIn(
            email=f"{random_lower_string(6)}@test.com",
            type=UserAccountType.STUDENT,
            first_name="Test",
            last_name_initial="S",
            school_id=test_school.id,
            class_group_id=test_class_group.id,
        ),
        commit=False,
    )
    assert isinstance(student, Student), (
        "CRUD: User account with type='student' not constructing a Student object"
    )

    assert student.name == "Test S"
    assert student.first_name == "Test"
    assert student.last_name_initial == "S"

    crud.user.remove(session, id=student.id)


def test_cross_model_updates(session, test_school, test_class_group):
    fake_domain = random_lower_string(12)
    student = crud.user.create(
        db=session,
        obj_in=UserCreateIn(
            email=f"teststudentupdate5@{fake_domain}.com",
            type=UserAccountType.STUDENT,
            school_id=test_school.id,
            name="JoeShooer P",
            class_group_id=test_class_group.id,
        ),
        commit=True,
    )

    update = UserUpdateIn(first_name="Joshua", last_name_initial="L")

    updated_student = crud.user.update(db=session, obj_in=update, db_obj=student)
    assert (
        updated_student.first_name == "Joshua"
        and updated_student.last_name_initial == "L"
        and isinstance(updated_student, Student)
    )


def test_access_subclass_through_superclass_query(
    session, test_school, test_class_group
):
    student = crud.user.create(
        db=session,
        obj_in=UserCreateIn(
            name="Test Student to retrieve via parent tables",
            email="teststudentretrieve@test.com",
            type=UserAccountType.STUDENT,
            school_id=test_school.id,
            first_name="Test",
            last_name_initial="T",
            class_group_id=test_class_group.id,
        ),
        commit=False,
    )
    session.add(student)
    session.flush()

    assert student.id

    assert (
        crud.user.get_by_account_email(session, "teststudentretrieve@test.com")
        is not None
    )

    reader = session.execute(
        select(Reader).where(Reader.email == "teststudentretrieve@test.com")
    ).scalar_one_or_none()

    assert reader == student

    session.rollback()


def test_user_info_dict_merging(session):
    original_huey_attributes = {"last_visited": str(date.today())}
    updated_huey_attributes = {"reading_ability": ["SPOT"]}

    email = "testreaderupdatemerge3@test.com"
    if user := crud.user.get_by_account_email(db=session, email=email):
        crud.user.remove(db=session, id=user.id)

    reader = crud.user.create(
        db=session,
        obj_in=UserCreateIn(
            name="Test Reader to update reading preferences",
            email=email,
            type=UserAccountType.PUBLIC,
            first_name="Test",
            last_name_initial="T",
            huey_attributes=original_huey_attributes,
        ),
        commit=False,
    )
    session.add(reader)
    session.flush()
    assert reader.id

    updated_reader_without_merge = crud.user.update(
        db=session,
        obj_in=UserUpdateIn(huey_attributes=updated_huey_attributes),
        db_obj=reader,
        merge_dicts=False,
    )
    assert updated_reader_without_merge.huey_attributes["reading_ability"]
    with pytest.raises(KeyError):
        updated_reader_without_merge.huey_attributes["last_visited"]

    updated_reader_with_merge = crud.user.update(
        db=session,
        obj_in=UserUpdateIn(huey_attributes=original_huey_attributes),
        db_obj=reader,
        merge_dicts=True,
    )
    assert updated_reader_with_merge.huey_attributes["reading_ability"]
    assert updated_reader_with_merge.huey_attributes["last_visited"]

    session.rollback()


def test_public_reader_to_student_update(
    session, test_user_account, test_school, test_class_group
):
    assert isinstance(test_user_account, PublicReader), (
        "CRUD: User account without specified type not constructing a PublicReader object"
    )

    # Now change the user type to student
    student = crud.user.update(
        db=session,
        obj_in=InternalUserUpdateIn(
            current_type="public",
            type="student",
            school_id=test_school.id,
            class_group_id=test_class_group.id,
            username="thisisannoyingtoprovide",
        ),
        db_obj=test_user_account,
    )

    assert isinstance(student, Student), (
        "User account hasn't been changed to Student type"
    )


def test_student_to_public_reader_update(
    session, test_user_account, test_school, test_class_group
):
    # Update the user type to student
    student = crud.user.update(
        db=session,
        obj_in=InternalUserUpdateIn(
            current_type="public",
            type="student",
            school_id=test_school.id,
            class_group_id=test_class_group.id,
            username="thisisannoyingtoprovide",
        ),
        db_obj=test_user_account,
    )

    assert isinstance(student, Student), (
        "User account hasn't been changed to Student type"
    )

    # Update the user type back to public reader
    user = crud.user.update(
        db=session,
        obj_in=InternalUserUpdateIn(
            current_type="student",
            type="public",
        ),
        db_obj=student,
    )
    assert isinstance(user, PublicReader), (
        "User account hasn't been changed to Public Reader type"
    )


def test_user_creation_name_validation(session):
    fake_domain = random_lower_string(12)

    # test that 'name' can be inferred from 'first_name' and 'last_name_initial'
    nameless = crud.user.create(
        db=session,
        obj_in=UserCreateIn(
            email=f"testnameless@{fake_domain}.com",
            first_name="Nameless",
            last_name_initial="T",
        ),
        commit=True,
    )
    assert nameless.name == "Nameless T"

    # test that 'first_name' and 'last_name_initial' can be inferred from 'name'
    named: PublicReader = crud.user.create(
        db=session,
        obj_in=UserCreateIn(
            email=f"testnamed@{fake_domain}.com",
            type=UserAccountType.PUBLIC,
            name="Nameless Traveler",
        ),
        commit=True,
    )
    assert named.first_name == "Nameless"
    assert named.last_name_initial == "T"

    # but ensure that first/last names are still optional
    named.first_name = None
    named.last_name_initial = None
    session.flush()
