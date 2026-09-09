"""Create synthetic organisation fixtures in a dedicated local or PR database."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select

from app.config import get_settings
from app.db.session import get_session_maker
from app.models.collection import Collection
from app.models.collection_item import CollectionItem
from app.models.edition import Edition
from app.models.educator import Educator
from app.models.organisation import (
    LibraryMembership,
    Organisation,
    OrganisationMembership,
    OrganisationSubscription,
)
from app.models.product import Product
from app.models.school import School, SchoolState
from app.models.subscription import Subscription, SubscriptionType
from app.models.user import User


def identifier(key: str):
    value = uuid5(NAMESPACE_URL, f"https://example.test/huey-organisation-demo/{key}")
    return UUID(bytes=value.bytes, version=4)


def main() -> None:
    settings = get_settings()
    database = settings.SQLALCHEMY_DATABASE_URI.database
    if database != "organisation_prototype" and not (
        database.startswith("wriveted_pr_")
        and database.removeprefix("wriveted_pr_").isdigit()
        and settings.GCP_CLOUD_SQL_INSTANCE_ID == "wriveted-development"
    ):
        raise SystemExit(
            "Use the dedicated organisation_prototype or a development PR database"
        )

    with get_session_maker()() as session:
        organisations = {}
        for key, name, kind in (
            ("school", "Demo International School", "school"),
            ("public", "Demo City Libraries", "public_library"),
        ):
            organisation = session.get(Organisation, identifier(key))
            if organisation is None:
                organisation = Organisation(id=identifier(key), name=name, kind=kind)
                session.add(organisation)
            organisations[key] = organisation
        session.flush()

        libraries = {}
        for key, name, parent, country in (
            ("primary", "Primary and Middle School Library", "school", "IND"),
            ("high", "High School Library", "school", "IND"),
            ("central", "Central Library", "public", "AUS"),
            ("north", "North Branch", "public", "AUS"),
            ("west", "West Branch", "public", "AUS"),
            ("unrelated", "Unrelated School Library", None, "NZL"),
        ):
            library = session.scalar(
                select(School).where(School.school_uuid == identifier(key))
            )
            if library is None:
                library = School(
                    school_uuid=identifier(key),
                    name=name,
                    country_code=country,
                    state=SchoolState.ACTIVE,
                    organisation_id=organisations[parent].id if parent else None,
                    info={"seed_key": f"organisation-demo-{key}"},
                )
                session.add(library)
                session.flush()
            libraries[key] = library
            collection_id = identifier(f"{key}/main")
            if session.get(Collection, collection_id) is None:
                session.add(
                    Collection(
                        id=collection_id,
                        name="Main collection",
                        school_id=library.school_uuid,
                        is_default=True,
                    )
                )
        if session.get(Collection, identifier("high/classroom")) is None:
            session.add(
                Collection(
                    id=identifier("high/classroom"),
                    name="Classroom reading",
                    school_id=libraries["high"].school_uuid,
                    is_default=False,
                )
            )
        session.flush()

        people = {}
        for key, name, library in (
            ("central-manager", "Demo Central Manager", "primary"),
            ("high-librarian", "Demo High School Librarian", "high"),
            ("reviewer", "Demo Book Reviewer", "high"),
            ("outsider", "Demo Unrelated Educator", "unrelated"),
        ):
            email = f"{key}@organisation-demo.example.org"
            person = session.get(User, identifier(key))
            if person is None:
                person = Educator(
                    id=identifier(key),
                    email=email,
                    name=name,
                    school_id=libraries[library].id,
                    is_active=True,
                    info={"seed_key": f"organisation-demo-{key}"},
                )
                session.add(person)
            else:
                person.email = email
            people[key] = person
        session.flush()
        for organisation in organisations.values():
            key = (organisation.id, people["central-manager"].id)
            if session.get(OrganisationMembership, key) is None:
                session.add(
                    OrganisationMembership(organisation_id=key[0], user_id=key[1])
                )
        for person_key, role in (
            ("high-librarian", "manager"),
            ("reviewer", "reviewer"),
        ):
            key = (libraries["high"].id, people[person_key].id)
            if session.get(LibraryMembership, key) is None:
                session.add(
                    LibraryMembership(school_id=key[0], user_id=key[1], role=role)
                )

        product_id = "qa_organisation_demo_paid_product"
        if session.get(Product, product_id) is None:
            session.add(
                Product(id=product_id, name="Synthetic organisation QA subscription")
            )
        session.flush()
        for organisation_key, library_key in (
            ("school", "primary"),
            ("public", "central"),
        ):
            organisation = organisations[organisation_key]
            library = libraries[library_key]
            subscription_id = f"qa_organisation_demo_{organisation_key}"
            if session.get(Subscription, subscription_id) is None:
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                session.add(
                    Subscription(
                        id=subscription_id,
                        school_id=library.school_uuid,
                        type=SubscriptionType.SCHOOL
                        if organisation_key == "school"
                        else SubscriptionType.LIBRARY,
                        stripe_customer_id=f"qa_organisation_demo_customer_{organisation_key}",
                        is_active=True,
                        expiration=now + timedelta(days=30),
                        product_id=product_id,
                        stripe_status="active",
                        paid_at=now,
                        info={
                            "seed_key": f"organisation-demo-paid-{organisation_key}",
                            "synthetic": True,
                        },
                    )
                )

            session.flush()
            if session.get(OrganisationSubscription, subscription_id) is None:
                session.add(
                    OrganisationSubscription(
                        organisation_id=organisation.id, subscription_id=subscription_id
                    )
                )

        fixture = json.loads(
            (Path(__file__).parent / "fixtures/admin-ui-seed.json").read_text()
        )
        editions = []
        for work_config in fixture["works"]:
            edition = session.scalar(
                select(Edition).where(Edition.isbn == work_config["isbn"])
            )
            if (
                edition is None
                or edition.work is None
                or (edition.work.info or {}).get("seed_key") != work_config["seed_key"]
            ):
                raise SystemExit(
                    "Expected identified synthetic admin demo books; run the admin demo seed first"
                )
            edition.work.title = work_config["title"]
            edition.edition_title = work_config["title"]
            edition.title = work_config["title"]
            editions.append(edition)
        editions = sorted(editions, key=lambda edition: edition.isbn)[:10]
        for index, library_key in enumerate(libraries):
            for edition in editions[index % 3 : index % 3 + 5]:
                collection_id = identifier(f"{library_key}/main")
                existing = session.scalar(
                    select(CollectionItem.id).where(
                        CollectionItem.collection_id == collection_id,
                        CollectionItem.edition_isbn == edition.isbn,
                    )
                )
                if existing is None:
                    session.add(
                        CollectionItem(
                            collection_id=collection_id,
                            edition_isbn=edition.isbn,
                            copies_total=1,
                            copies_available=1,
                        )
                    )
        session.commit()
        print(
            json.dumps(
                {
                    "organisations": {
                        key: str(value.id) for key, value in organisations.items()
                    },
                    "libraries": {
                        key: str(value.school_uuid) for key, value in libraries.items()
                    },
                    "people": {
                        key: {"id": str(value.id), "email": value.email}
                        for key, value in people.items()
                    },
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
