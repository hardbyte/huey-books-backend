from typing import Any, Dict, Optional, Tuple, Union
from uuid import UUID

from fastapi import Query
from fastapi.encoders import jsonable_encoder
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from structlog import get_logger

from app.crud import CRUDBase
from app.crud.base import deep_merge_dicts
from app.models import (
    Educator,
    Parent,
    PublicReader,
    SchoolAdmin,
    Student,
    Subscription,
    User,
    WrivetedAdmin,
)
from app.models.school import School
from app.models.subscription import SubscriptionType
from app.models.supporter import Supporter
from app.models.user import UserAccountType
from app.schemas.users.user_create import UserCreateIn
from app.schemas.users.user_update import InternalUserUpdateIn, UserUpdateIn
from app.services.users import new_identifiable_username

logger = get_logger()


class CRUDUser(CRUDBase[User, UserCreateIn, UserUpdateIn]):
    # TODO handle create student account linked to school

    # ----CRUD override----

    async def aget_query(self, db: AsyncSession, id: Any):
        query = select(User).where(User.id == id)
        user = (await db.execute(query)).scalar_one_or_none()

        return self._select_concrete_user_instance(id, query, user)

    def get_query(self, db: Session, id: Any) -> Query:
        query = select(User).where(User.id == id)
        user = db.execute(query).scalar_one_or_none()

        return self._select_concrete_user_instance(id, query, user)

    def _select_concrete_user_instance(self, id, query, user):
        if not user:
            return query
        match user.type:
            case UserAccountType.WRIVETED:
                return select(WrivetedAdmin).where(WrivetedAdmin.id == id)
            case UserAccountType.STUDENT:
                return select(Student).where(Student.id == id)
            case UserAccountType.PUBLIC:
                return select(PublicReader).where(PublicReader.id == id)
            case UserAccountType.EDUCATOR:
                return select(Educator).where(Educator.id == id)
            case UserAccountType.SCHOOL_ADMIN:
                return select(SchoolAdmin).where(SchoolAdmin.id == id)
            case UserAccountType.PARENT:
                return select(Parent).where(Parent.id == id)
            case UserAccountType.SUPPORTER:
                return select(Supporter).where(Supporter.id == id)
            case _:
                return query

    def build_orm_object(self, obj_in: UserCreateIn, session: Session) -> User:
        """An uncommitted ORM object from the input data"""
        type = obj_in.type or UserAccountType.PUBLIC

        # canonise the school id and validate school
        provided_id = obj_in.school_id
        if provided_id:
            if (
                isinstance(provided_id, UUID)
                and not (
                    school := session.scalar(
                        select(School).where(School.wriveted_identifier == provided_id)
                    )
                )
            ) or (
                isinstance(provided_id, int)
                and not (
                    school := session.scalar(
                        select(School).where(School.id == provided_id)
                    )
                )
            ):
                raise ValueError(f"School with the id {provided_id} does not exist")
            obj_in.school_id = school.id

        match type:
            case UserAccountType.PUBLIC:
                model = PublicReader
            case UserAccountType.STUDENT:
                model = Student
                self._generate_username_if_missing(session, obj_in)
            case UserAccountType.EDUCATOR:
                model = Educator
            case UserAccountType.SCHOOL_ADMIN:
                model = SchoolAdmin
            case UserAccountType.WRIVETED:
                model = WrivetedAdmin
            case UserAccountType.PARENT:
                model = Parent
            case UserAccountType.SUPPORTER:
                model = Supporter
            case _:
                model = User

        obj_in_data = jsonable_encoder(obj_in)

        # clean up None attributes
        obj_in_data = {k: v for k, v in obj_in_data.items() if v is not None}

        db_obj = model(**obj_in_data)
        return db_obj

    def update(
        self,
        db: Session,
        *,
        db_obj: User,
        obj_in: Union[InternalUserUpdateIn, Dict[str, Any]],
        merge_dicts: bool = False,
        commit: bool = True,
    ) -> User:
        if isinstance(obj_in, dict):
            update_data = obj_in
        else:
            update_data = obj_in.dict(exclude_unset=True)

        # canonise the school id and validate school
        provided_id = update_data.get("school_id")
        if provided_id:
            if (
                isinstance(provided_id, UUID)
                and not (
                    school := db.scalar(
                        select(School).where(School.wriveted_identifier == provided_id)
                    )
                )
            ) or (
                isinstance(provided_id, int)
                and not (
                    school := db.scalar(select(School).where(School.id == provided_id))
                )
            ):
                raise ValueError(f"School with the id {provided_id} does not exist")
            update_data["school_id"] = school.id

        logger.debug("Updating a user", data=update_data)
        # if updating user type
        if update_data.get("type") and update_data["type"] != db_obj.type:
            logger.debug("Changing user type", new_type=update_data["type"])
            # hold onto the current user data
            original_data = vars(db_obj)

            # remove the existing object from the db
            # (need to flush, or sqlalchemy will notice the identical
            # id's of the deleted and new user objects, and compose a
            # failing UPDATE instead of a fresh INSERT)
            logger.debug("Deleting old typed user object from database.")
            db.delete(db_obj)
            db.flush()

            # combine existing and update data to create instantiation data for the new obj
            combined_data = original_data.copy()
            deep_merge_dicts(combined_data, update_data)

            # trim the instantiation data to just the fields belonging to the target class
            user_type_class_map = {
                UserAccountType.PUBLIC: PublicReader,
                UserAccountType.STUDENT: Student,
                UserAccountType.EDUCATOR: Educator,
                UserAccountType.SCHOOL_ADMIN: SchoolAdmin,
                UserAccountType.PARENT: Parent,
                UserAccountType.WRIVETED: WrivetedAdmin,
                UserAccountType.SUPPORTER: Supporter,
            }
            trimmed_data = {
                k: combined_data[k]
                for k in combined_data
                if k in dir(user_type_class_map[obj_in.type])
            }
            logger.debug("Creating new user type", new_user_data=trimmed_data)
            NewUserType = user_type_class_map[obj_in.type]
            db_obj = NewUserType(**trimmed_data)

        else:
            for field in update_data:
                if hasattr(db_obj, field):
                    if merge_dicts and isinstance(getattr(db_obj, field), dict):
                        deep_merge_dicts(getattr(db_obj, field), update_data[field])
                    else:
                        setattr(db_obj, field, update_data[field])

        db.add(db_obj)
        if commit:
            db.commit()
            db.refresh(db_obj)

        return db_obj

    # ---------------------

    def _generate_username_if_missing(self, session, obj_in: UserCreateIn):
        if obj_in.username is None:
            obj_in.username = new_identifiable_username(
                obj_in.first_name, obj_in.last_name_initial, session, obj_in.school_id
            )

    def get_or_create(
        self, db: Session, user_data: UserCreateIn, commit=True
    ) -> Tuple[User, bool]:
        """
        Get a user by email, creating a new user if required.
        """
        db.execute(
            select(
                func.pg_advisory_xact_lock(
                    func.hashtextextended(user_data.email.lower(), 0)
                )
            )
        )
        q = select(User).where(func.lower(User.email) == user_data.email.lower())
        try:
            user = db.execute(q).scalar_one()
            return user, False
        except NoResultFound:
            logger.info("Creating new user", data=user_data)
            user = self.create(db, obj_in=user_data, commit=commit)
            return user, True

    def get_by_account_email(self, db: Session, email: str) -> Optional[User]:
        """return User with given email (or account identifier) or None"""
        return db.execute(
            select(User).where(func.lower(User.email) == email.lower())
        ).scalar_one_or_none()

    def get_all_with_optional_filters_query(
        self,
        db: Session,
        query_string: Optional[str] = None,
        type: Optional[UserAccountType] = None,
        is_active: Optional[bool] = None,
        students_of: Optional["School"] = None,
        active_subscription_type: Optional[SubscriptionType] = None,
    ):
        user_query = self.get_all_query(
            db=db, order_by=[User.is_active.desc(), User.name.asc()]
        )

        if query_string is not None:
            # https://docs.sqlalchemy.org/en/14/dialects/postgresql.html?highlight=search#full-text-search
            user_query = user_query.where(
                or_(
                    func.lower(User.name).contains(query_string.lower()),
                    func.lower(User.email).contains(query_string.lower()),
                )
            )
        if type is not None:
            user_query = user_query.where(User.type == type)
        if is_active is not None:
            user_query = user_query.where(User.is_active == is_active)
        if students_of is not None:
            user_query = (
                user_query.join(Student)
                .where(User.id == Student.id)
                .where(Student.school == students_of)
            )
        if active_subscription_type is not None:
            user_query = (
                user_query.join(Subscription, User.id == Subscription.parent_id)
                .where(Subscription.is_active is True)
                .where(Subscription.type == active_subscription_type)
            )
        return user_query

    def get_filtered_with_count(
        self,
        db: Session,
        query_string: Optional[str] = None,
        type: Optional[UserAccountType] = None,
        is_active: Optional[bool] = None,
        students_of: Optional[School] = None,
        active_subscription_type: Optional[SubscriptionType] = None,
        skip: int = 0,
        limit: int = 100,
    ):
        """
        Get users with optional filtering.

        :return: Tuple[int, list]
        Returns a count of the total matching rows, and the requested
        page of users.
        """
        select_statement = self.get_all_with_optional_filters_query(
            db=db,
            query_string=query_string,
            type=type,
            is_active=is_active,
            students_of=students_of,
            active_subscription_type=active_subscription_type,
        )
        matching_count = self.count_query(db=db, query=select_statement)

        paginated_users_query = self.apply_pagination(
            select_statement, skip=skip, limit=limit
        )

        return matching_count, db.scalars(paginated_users_query).all()

    def get_all_with_optional_filters(
        self,
        db: Session,
        skip: int = 0,
        limit: int = 100,
        **kwargs,
    ):
        query = self.apply_pagination(
            self.get_all_with_optional_filters_query(db=db, **kwargs),
            skip=skip,
            limit=limit,
        )
        return db.execute(query).scalars().all()

    def get_student_by_username_and_school_id(
        self, db: Session, username: str, school_id: int
    ):
        q = select(Student).where(
            and_(
                func.lower(Student.username) == username.lower(),
                Student.school_id == school_id,
            )
        )
        return db.execute(q).scalar_one_or_none()


user = CRUDUser(User)
