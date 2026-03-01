from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Optional
from uuid import UUID

from sqlalchemy import and_

from app.api.dependencies.security import create_user_access_token
from app.db.session import get_session_maker
from app.models.booklist import BookList, ListSharingType, ListType
from app.models.booklist_work_association import BookListItem
from app.models.class_group import ClassGroup
from app.models.cms import (
    ChatTheme,
    CMSContent,
    ContentStatus,
    ContentType,
    ContentVisibility,
    ExecutionContext,
    FlowConnection,
    FlowDefinition,
    FlowNode,
    NodeType,
)
from app.models.collection import Collection
from app.models.collection_item import CollectionItem
from app.models.edition import Edition
from app.models.educator import Educator
from app.models.hue import Hue
from app.models.labelset import LabelOrigin, LabelSet
from app.models.labelset_hue_association import LabelSetHue, Ordinal
from app.models.parent import Parent
from app.models.public_reader import PublicReader
from app.models.reading_ability import ReadingAbility
from app.models.school import School, SchoolBookbotType, SchoolState
from app.models.school_admin import SchoolAdmin
from app.models.service_account import ServiceAccount, ServiceAccountType
from app.models.student import Student
from app.models.supporter import Supporter
from app.models.user import User
from app.models.work import Work, WorkType
from app.models.wriveted_admin import WrivetedAdmin
from app.services.flow_utils import token_to_enum

DEFAULT_CONFIG_PATH = Path("scripts/fixtures/admin-ui-seed.json")
DEFAULT_QUESTIONS_CSV = Path("AI-Questions.csv")


def _info_with_seed(info: Optional[dict], seed_key: Optional[str]) -> dict:
    payload = dict(info or {})
    if seed_key:
        payload["seed_key"] = seed_key
    return payload


def _get_by_seed_key(session, model, seed_key: Optional[str]) -> Optional[Any]:
    if not seed_key or not hasattr(model, "info"):
        return None
    try:
        return (
            session.query(model)
            .filter(model.info["seed_key"].astext == seed_key)
            .one_or_none()
        )
    except Exception:
        return None


def _get_by_email(session, email: str) -> Optional[User]:
    return session.query(User).filter(User.email == email).one_or_none()


def _ensure_school(session, config: dict) -> School:
    seed_key = config.get("seed_key")
    wriveted_identifier = config.get("wriveted_identifier")

    school = None
    if wriveted_identifier:
        school = (
            session.query(School)
            .filter(School.wriveted_identifier == UUID(wriveted_identifier))
            .one_or_none()
        )
    if school is None and seed_key:
        school = _get_by_seed_key(session, School, seed_key)
    if school is None:
        school = (
            session.query(School).filter(School.name == config["name"]).one_or_none()
        )

    if school is None:
        school = School(
            name=config["name"],
            state=SchoolState(config.get("state", "active")),
            bookbot_type=SchoolBookbotType(config.get("bookbot_type", "huey_books")),
            info=_info_with_seed(config.get("info"), seed_key),
        )
        if wriveted_identifier:
            school.wriveted_identifier = UUID(wriveted_identifier)
        session.add(school)
        session.flush()
        return school

    school.name = config.get("name", school.name)
    if config.get("state"):
        school.state = SchoolState(config["state"])
    if config.get("bookbot_type"):
        school.bookbot_type = SchoolBookbotType(config["bookbot_type"])
    if config.get("info") or seed_key:
        school.info = _info_with_seed(config.get("info", school.info or {}), seed_key)
    return school


def _ensure_class_groups(
    session, school: School, configs: Iterable[dict]
) -> dict[str, ClassGroup]:
    groups: dict[str, ClassGroup] = {}
    for cfg in configs:
        name = cfg["name"]
        group = (
            session.query(ClassGroup)
            .filter(
                and_(
                    ClassGroup.name == name,
                    ClassGroup.school_id == school.wriveted_identifier,
                )
            )
            .one_or_none()
        )
        if group is None:
            group = ClassGroup(
                name=name,
                join_code=cfg.get("join_code"),
                school=school,
            )
            session.add(group)
            session.flush()
        groups[name] = group
    return groups


def _resolve_school(config: dict, schools: dict[str, School]) -> Optional[School]:
    seed_key = config.get("school_seed_key")
    if not seed_key:
        return None
    return schools.get(seed_key)


def _ensure_user(
    session,
    config: dict,
    schools: dict[str, School],
    class_groups: dict[str, ClassGroup],
    parents: dict[str, Parent],
) -> User:
    email = config.get("email")
    if not email:
        raise ValueError("User config missing email")

    existing = _get_by_email(session, email)
    if existing is not None:
        if config.get("name"):
            existing.name = config["name"]
        return existing

    user_type = config.get("type", "public_reader").lower()
    name = config.get("name", email)
    seed_key = config.get("seed_key")
    info = _info_with_seed(config.get("info"), seed_key)

    if user_type == "wriveted_admin":
        user = WrivetedAdmin(email=email, name=name, info=info)
    elif user_type == "school_admin":
        school = _resolve_school(config, schools)
        if not school:
            raise ValueError(f"School admin {email} missing school_seed_key")
        user = SchoolAdmin(email=email, name=name, school_id=school.id, info=info)
    elif user_type == "educator":
        school = _resolve_school(config, schools)
        if not school:
            raise ValueError(f"Educator {email} missing school_seed_key")
        user = Educator(email=email, name=name, school_id=school.id, info=info)
    elif user_type == "student":
        school = _resolve_school(config, schools)
        if not school:
            raise ValueError(f"Student {email} missing school_seed_key")
        class_name = config.get("class_group")
        class_group = class_groups.get(class_name) if class_name else None
        if not class_group:
            raise ValueError(f"Student {email} missing class_group")
        parent_email = config.get("parent_email")
        parent = parents.get(parent_email) if parent_email else None
        user = Student(
            email=email,
            name=name,
            username=config.get("username") or email.split("@", 1)[0],
            school_id=school.id,
            class_group_id=class_group.id,
            parent=parent,
            info=info,
        )
    elif user_type == "parent":
        user = Parent(email=email, name=name, info=info)
    elif user_type == "supporter":
        user = Supporter(email=email, name=name, info=info)
    elif user_type == "public_reader":
        user = PublicReader(email=email, name=name, info=info)
    else:
        raise ValueError(f"Unknown user type: {user_type}")

    session.add(user)
    session.flush()
    return user


def _ensure_service_account(
    session, name: str, account_type: ServiceAccountType = ServiceAccountType.KIOSK
) -> ServiceAccount:
    sa = (
        session.query(ServiceAccount)
        .filter(ServiceAccount.name == name)
        .one_or_none()
    )
    if sa is None:
        sa = ServiceAccount(name=name, type=account_type)
        session.add(sa)
        session.flush()
    return sa


def _ensure_hue(session, config: dict) -> Hue:
    hue = session.query(Hue).filter(Hue.key == config["key"]).one_or_none()
    if hue is None:
        hue = Hue(key=config["key"], name=config.get("name", config["key"]))
        session.add(hue)
        session.flush()
    return hue


def _ensure_reading_ability(session, config: dict) -> ReadingAbility:
    ra = (
        session.query(ReadingAbility)
        .filter(ReadingAbility.key == config["key"])
        .one_or_none()
    )
    if ra is None:
        ra = ReadingAbility(key=config["key"], name=config.get("name", config["key"]))
        session.add(ra)
        session.flush()
    return ra


def _ensure_work_and_edition(
    session,
    config: dict,
    hues: dict[str, Hue],
    reading_abilities: dict[str, ReadingAbility],
) -> Edition:
    isbn = config["isbn"]
    edition = session.query(Edition).filter(Edition.isbn == isbn).one_or_none()
    if edition is None:
        work = Work(
            title=config["title"],
            type=WorkType.BOOK,
            info=_info_with_seed(config.get("info"), config.get("seed_key")),
        )
        edition = Edition(
            isbn=isbn,
            work=work,
            cover_url=config.get("cover_url"),
        )
        session.add_all([work, edition])
        session.flush()
    else:
        work = edition.work
        if work:
            work.title = config.get("title", work.title)
            if config.get("info") or config.get("seed_key"):
                work.info = _info_with_seed(
                    config.get("info", work.info or {}), config.get("seed_key")
                )
        edition.cover_url = config.get("cover_url", edition.cover_url)

    label_cfg = config.get("labelset") or {}
    if work:
        labelset = work.labelset
        if labelset is None:
            labelset = LabelSet(
                work=work,
                min_age=label_cfg.get("min_age"),
                max_age=label_cfg.get("max_age"),
                huey_summary=label_cfg.get("huey_summary", ""),
                hue_origin=LabelOrigin.HUMAN,
                reading_ability_origin=LabelOrigin.HUMAN,
            )
            session.add(labelset)
            session.flush()
        else:
            if label_cfg.get("min_age") is not None:
                labelset.min_age = label_cfg.get("min_age")
            if label_cfg.get("max_age") is not None:
                labelset.max_age = label_cfg.get("max_age")
            if label_cfg.get("huey_summary") is not None:
                labelset.huey_summary = label_cfg["huey_summary"]

        reading_keys = label_cfg.get("reading_abilities") or []
        labelset.reading_abilities = [reading_abilities[key] for key in reading_keys]

        for hue_cfg in label_cfg.get("hues", []):
            hue = hues.get(hue_cfg["key"])
            if not hue:
                continue
            ordinal = Ordinal(hue_cfg.get("ordinal", "primary"))
            assoc = (
                session.query(LabelSetHue)
                .filter(
                    LabelSetHue.labelset_id == labelset.id,
                    LabelSetHue.ordinal == ordinal,
                )
                .one_or_none()
            )
            if assoc is None:
                assoc = LabelSetHue(
                    labelset_id=labelset.id,
                    hue_id=hue.id,
                    ordinal=ordinal,
                )
                session.add(assoc)
            else:
                assoc.hue_id = hue.id

    return edition


def _ensure_collection(
    session,
    config: dict,
    school: School,
    editions_by_isbn: dict[str, Edition],
) -> Collection:
    seed_key = config.get("seed_key")
    collection = None
    if seed_key:
        collection = _get_by_seed_key(session, Collection, seed_key)
    if collection is None:
        collection = (
            session.query(Collection)
            .filter(
                and_(
                    Collection.name == config["name"],
                    Collection.school_id == school.wriveted_identifier,
                )
            )
            .one_or_none()
        )
    if collection is None:
        collection = Collection(
            name=config["name"],
            school=school,
            info=_info_with_seed(config.get("info"), seed_key),
        )
        session.add(collection)
        session.flush()
    elif config.get("info") or seed_key:
        collection.info = _info_with_seed(
            config.get("info", collection.info or {}), seed_key
        )

    for item in config.get("items", []):
        edition = editions_by_isbn.get(item["isbn"])
        if not edition:
            continue
        existing = (
            session.query(CollectionItem)
            .filter(
                CollectionItem.collection_id == collection.id,
                CollectionItem.edition_isbn == edition.isbn,
            )
            .one_or_none()
        )
        if existing is None:
            existing = CollectionItem(
                collection=collection,
                edition=edition,
                copies_total=item.get("copies_total", 1),
                copies_available=item.get("copies_available", 1),
            )
            session.add(existing)
        else:
            if item.get("copies_total") is not None:
                existing.copies_total = item["copies_total"]
            if item.get("copies_available") is not None:
                existing.copies_available = item["copies_available"]
    return collection


def _ensure_booklist(
    session,
    config: dict,
    school: School,
    editions_by_isbn: dict[str, Edition],
) -> BookList:
    seed_key = config.get("seed_key")
    booklist = None
    if seed_key:
        booklist = _get_by_seed_key(session, BookList, seed_key)
    if booklist is None:
        booklist = (
            session.query(BookList)
            .filter(
                and_(BookList.name == config["name"], BookList.school_id == school.id)
            )
            .one_or_none()
        )
    if booklist is None:
        booklist = BookList(
            name=config["name"],
            type=ListType(config.get("type", "school")),
            sharing=ListSharingType(config.get("sharing", "public")),
            school=school,
            info=_info_with_seed(config.get("info"), seed_key),
        )
        session.add(booklist)
        session.flush()
    else:
        if config.get("info") or seed_key:
            booklist.info = _info_with_seed(
                config.get("info", booklist.info or {}), seed_key
            )

    # Build a map of work_id → desired order_id from the config
    from sqlalchemy import text

    desired_items: dict[int, int] = {}
    for idx, item in enumerate(config.get("items", []), start=1):
        edition = editions_by_isbn.get(item["isbn"])
        if not edition or not edition.work_id:
            continue
        desired_items[edition.work_id] = item.get("order", idx)

    # Defer unique constraint, then update or insert each item
    session.execute(text("SET CONSTRAINTS ALL DEFERRED"))

    existing_by_work = {
        bli.work_id: bli
        for bli in session.query(BookListItem)
        .filter(BookListItem.booklist_id == booklist.id)
        .all()
    }

    for work_id, order_id in desired_items.items():
        bli = existing_by_work.pop(work_id, None)
        if bli is not None:
            bli.order_id = order_id
        else:
            session.add(
                BookListItem(
                    booklist_id=booklist.id,
                    work_id=work_id,
                    order_id=order_id,
                )
            )

    # Remove items no longer in the config
    for bli in existing_by_work.values():
        session.delete(bli)

    return booklist


def _ensure_cms_content(
    session,
    config: dict,
    school: Optional[School],
    created_by: Optional[User],
) -> CMSContent:
    seed_key = config.get("seed_key")
    content = None
    if seed_key:
        content = _get_by_seed_key(session, CMSContent, seed_key)
    if content is None:
        content = (
            session.query(CMSContent)
            .filter(
                and_(
                    CMSContent.type == ContentType(config.get("type", "message")),
                    CMSContent.content == config.get("content", {}),
                )
            )
            .one_or_none()
        )
    if content is None:
        content = CMSContent(
            type=ContentType(config.get("type", "message")),
            content=config.get("content", {}),
            info=_info_with_seed(config.get("info"), seed_key),
            tags=config.get("tags", []),
            status=ContentStatus(config.get("status", "draft")),
            visibility=ContentVisibility(config.get("visibility", "wriveted")),
            school_id=school.wriveted_identifier if school else None,
            created_by=created_by.id if created_by else None,
        )
        session.add(content)
        session.flush()
    else:
        content.content = config.get("content", content.content)
        content.tags = config.get("tags", content.tags)
        if config.get("status"):
            content.status = ContentStatus(config["status"])
        if config.get("visibility"):
            content.visibility = ContentVisibility(config["visibility"])
        if config.get("info") or seed_key:
            content.info = _info_with_seed(
                config.get("info", content.info or {}), seed_key
            )
    return content


def _ensure_theme(
    session,
    config: dict,
    school: Optional[School],
    created_by: Optional[User],
) -> ChatTheme:
    school_id = school.wriveted_identifier if school else None
    q = session.query(ChatTheme).filter(ChatTheme.name == config["name"])
    if school_id is not None:
        q = q.filter(ChatTheme.school_id == school_id)
    else:
        q = q.filter(ChatTheme.school_id.is_(None))
    theme = q.first()

    if theme is None:
        theme = ChatTheme(
            name=config["name"],
            description=config.get("description"),
            school_id=school.wriveted_identifier if school else None,
            config=config.get("config", {}),
            logo_url=config.get("logo_url"),
            avatar_url=config.get("avatar_url"),
            is_active=config.get("is_active", True),
            is_default=config.get("is_default", False),
            version=config.get("version", "1.0"),
            created_by=created_by.id if created_by else None,
        )
        session.add(theme)
        session.flush()
    else:
        theme.description = config.get("description", theme.description)
        theme.config = config.get("config", theme.config)
        if config.get("logo_url") is not None:
            theme.logo_url = config["logo_url"]
        if config.get("avatar_url") is not None:
            theme.avatar_url = config["avatar_url"]
        if "is_active" in config:
            theme.is_active = config["is_active"]
    return theme


def _load_flow_config(config: dict, base_dir: Path) -> Optional[dict]:
    """If config has a 'flow_file' key, merge the external JSON into config.

    Returns None when the referenced file is missing so the caller can skip it.
    """
    flow_file = config.get("flow_file")
    if not flow_file:
        return config
    path = base_dir / flow_file
    if not path.exists():
        print(f"  Warning: Flow file not found, skipping: {path}")
        return None
    external = json.loads(path.read_text())
    merged = {**external, **config}
    merged.pop("flow_file", None)
    return merged


def _ensure_flow(
    session,
    config: dict,
    school: Optional[School],
    created_by: Optional[User],
) -> FlowDefinition:
    seed_key = config.get("seed_key")
    flow = None
    if seed_key:
        flow = _get_by_seed_key(session, FlowDefinition, seed_key)
    if flow is None:
        flow = (
            session.query(FlowDefinition)
            .filter(FlowDefinition.name == config["name"])
            .one_or_none()
        )
    if flow is None:
        flow = FlowDefinition(
            name=config["name"],
            description=config.get("description"),
            version=config.get("version", "1.0.0"),
            entry_node_id=config.get("entry_node_id"),
            flow_data=config.get("flow_data", {}),
            info=_info_with_seed(config.get("info"), seed_key),
            visibility=ContentVisibility(config.get("visibility", "wriveted")),
            school_id=school.wriveted_identifier if school else None,
            created_by=created_by.id if created_by else None,
            trace_enabled=config.get("trace_enabled", False),
            is_published=config.get("is_published", True),
        )
        session.add(flow)
        session.flush()
    else:
        flow.name = config.get("name", flow.name)
        flow.description = config.get("description", flow.description)
        flow.version = config.get("version", flow.version)
        flow.entry_node_id = config.get("entry_node_id", flow.entry_node_id)
        flow.flow_data = config.get("flow_data", flow.flow_data)
        if config.get("visibility"):
            flow.visibility = ContentVisibility(config["visibility"])
        if config.get("info") or seed_key:
            flow.info = _info_with_seed(config.get("info", flow.info or {}), seed_key)
        if "is_published" in config:
            flow.is_published = config["is_published"]

    session.query(FlowConnection).filter(FlowConnection.flow_id == flow.id).delete()
    session.query(FlowNode).filter(FlowNode.flow_id == flow.id).delete()
    session.flush()

    flow_data = config.get("flow_data", {})
    for node in flow_data.get("nodes", []):
        node_type = NodeType(node.get("type", "message"))
        flow_node = FlowNode(
            flow_id=flow.id,
            node_id=node.get("id") or node.get("node_id"),
            node_type=node_type,
            execution_context=ExecutionContext(
                node.get("execution_context", "backend")
            ),
            template=node.get("template"),
            content=node.get("content", {}),
            position=node.get("position", {"x": 0, "y": 0}),
            info=node.get("info", {}),
        )
        session.add(flow_node)

    for connection in flow_data.get("connections", []):
        flow_connection = FlowConnection(
            flow_id=flow.id,
            source_node_id=connection.get("source"),
            target_node_id=connection.get("target"),
            connection_type=token_to_enum(connection.get("type")),
            conditions=connection.get("conditions", {}) or {},
            info=connection.get("info", {}) or {},
        )
        session.add(flow_connection)

    session.flush()
    return flow


def _resolve_composite_flow_refs(
    session, flows_by_seed_key: dict[str, "FlowDefinition"]
) -> None:
    """Resolve composite_flow_seed_key in composite nodes to actual flow UUIDs.

    After all flows are created, scan composite nodes for seed_key references
    and replace them with the corresponding flow's UUID.
    """
    from sqlalchemy.orm.attributes import flag_modified

    for flow in flows_by_seed_key.values():
        nodes = (
            session.query(FlowNode)
            .filter(
                FlowNode.flow_id == flow.id,
                FlowNode.node_type == NodeType.COMPOSITE,
            )
            .all()
        )
        for node in nodes:
            content = node.content or {}
            seed_key = content.get("composite_flow_seed_key")
            if not seed_key:
                continue
            target_flow = flows_by_seed_key.get(seed_key)
            if not target_flow:
                print(f"  Warning: composite_flow_seed_key '{seed_key}' not found")
                continue
            node.content = {
                **content,
                "composite_flow_id": str(target_flow.id),
            }
            flag_modified(node, "content")
    session.flush()


def _seed_airtable_questions(session, csv_path: Path) -> int:
    """Seed CMS preference questions from Airtable CSV export.

    Returns the number of questions inserted.
    """
    if not csv_path.exists():
        return 0

    # Import parsing logic from migration script
    sys_path_orig = sys.path[:]
    try:
        from scripts.migrate_airtable_questions import load_questions_from_csv
    except ImportError:
        # Fallback: add parent dir to path
        sys.path.insert(0, str(csv_path.parent.parent))
        try:
            from scripts.migrate_airtable_questions import load_questions_from_csv
        except ImportError:
            print("Warning: Could not import migration script, skipping questions")
            return 0
        finally:
            sys.path = sys_path_orig

    questions = load_questions_from_csv(csv_path)
    if not questions:
        return 0

    # Check for existing airtable questions
    existing = (
        session.query(CMSContent)
        .filter(CMSContent.info["source"].astext == "airtable")
        .count()
    )
    if existing > 0:
        print(f"  Found {existing} existing Airtable questions, replacing...")
        session.query(CMSContent).filter(
            CMSContent.info["source"].astext == "airtable"
        ).delete(synchronize_session="fetch")
        session.flush()

    count = 0
    for q in questions:
        content = CMSContent(
            id=q["id"],
            type=q["type"],
            content=q["content"],
            tags=q["tags"],
            is_active=q["is_active"],
            status=q["status"],
            visibility=q["visibility"],
            school_id=q["school_id"],
            info=q["info"],
        )
        session.add(content)
        count += 1

    session.flush()
    return count


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed admin UI demo data")
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="Path to seed config JSON",
    )
    parser.add_argument(
        "--emit-tokens",
        action="store_true",
        help="Print JWTs for seeded users",
    )
    parser.add_argument(
        "--tokens-format",
        choices=["text", "json"],
        default="text",
        help="Token output format",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    config_path = Path(args.config)
    if not config_path.exists():
        raise FileNotFoundError(f"Seed config not found: {config_path}")

    config = json.loads(config_path.read_text())
    SessionLocal = get_session_maker()
    tokens: Dict[str, Dict[str, str]] = {}
    schools: dict[str, School] = {}
    parents: dict[str, Parent] = {}

    with SessionLocal() as session:
        school_cfg = config.get("school")
        if not school_cfg:
            raise ValueError("Config missing school")
        school = _ensure_school(session, school_cfg)
        schools[school_cfg["seed_key"]] = school

        class_groups = _ensure_class_groups(
            session, school, config.get("class_groups", [])
        )

        # Seed users (parents first for student linking)
        for user_cfg in config.get("users", []):
            if user_cfg.get("type") == "parent":
                user = _ensure_user(session, user_cfg, schools, class_groups, parents)
                if isinstance(user, Parent):
                    parents[user.email] = user

        seeded_users: Dict[str, User] = {}
        for user_cfg in config.get("users", []):
            if user_cfg.get("type") == "parent":
                continue
            user = _ensure_user(session, user_cfg, schools, class_groups, parents)
            label = user_cfg.get("label", user.email)
            seeded_users[label] = user

        # Track parents too
        for label, parent in parents.items():
            seeded_users.setdefault("parent", parent)

        hues = {cfg["key"]: _ensure_hue(session, cfg) for cfg in config.get("hues", [])}
        reading_abilities = {
            cfg["key"]: _ensure_reading_ability(session, cfg)
            for cfg in config.get("reading_abilities", [])
        }

        editions_by_isbn: dict[str, Edition] = {}
        for work_cfg in config.get("works", []):
            edition = _ensure_work_and_edition(
                session, work_cfg, hues, reading_abilities
            )
            editions_by_isbn[work_cfg["isbn"]] = edition

        for collection_cfg in config.get("collections", []):
            collection_school = schools.get(collection_cfg.get("school_seed_key"))
            if not collection_school:
                raise ValueError("Collection missing school_seed_key")
            _ensure_collection(
                session, collection_cfg, collection_school, editions_by_isbn
            )

        for booklist_cfg in config.get("booklists", []):
            booklist_school = schools.get(booklist_cfg.get("school_seed_key"))
            if not booklist_school:
                raise ValueError("Booklist missing school_seed_key")
            _ensure_booklist(session, booklist_cfg, booklist_school, editions_by_isbn)

        admin_user = seeded_users.get("school_admin") or seeded_users.get(
            "wriveted_admin"
        )

        for cms_cfg in config.get("cms_content", []):
            cms_school = schools.get(cms_cfg.get("school_seed_key")) or school
            _ensure_cms_content(session, cms_cfg, cms_school, admin_user)

        # Seed Airtable preference questions if CSV exists
        questions_csv = config_path.parent.parent / "AI-Questions.csv"
        if not questions_csv.exists():
            questions_csv = DEFAULT_QUESTIONS_CSV
        q_count = _seed_airtable_questions(session, questions_csv)
        if q_count:
            print(f"  Seeded {q_count} preference questions from CSV")

        # Seed service accounts used by flow emit_event actions
        _ensure_service_account(session, "huey-events", ServiceAccountType.KIOSK)

        # Seed themes
        themes_by_seed_key: dict[str, ChatTheme] = {}
        for theme_cfg in config.get("themes", []):
            theme = _ensure_theme(session, theme_cfg, None, admin_user)
            if theme_cfg.get("seed_key"):
                themes_by_seed_key[theme_cfg["seed_key"]] = theme

        fixtures_dir = config_path.parent
        flows_by_seed_key: dict[str, FlowDefinition] = {}
        for flow_cfg in config.get("flows", []):
            resolved_cfg = _load_flow_config(flow_cfg, fixtures_dir)
            if resolved_cfg is None:
                continue
            # Link theme to flow via flow_data.theme_id if theme_seed_key is specified
            theme_seed_key = resolved_cfg.get("theme_seed_key")
            if theme_seed_key and theme_seed_key in themes_by_seed_key:
                theme = themes_by_seed_key[theme_seed_key]
                flow_data = resolved_cfg.get("flow_data", {})
                flow_data["theme_id"] = str(theme.id)
                resolved_cfg["flow_data"] = flow_data
            flow_school = schools.get(resolved_cfg.get("school_seed_key")) or school
            flow = _ensure_flow(session, resolved_cfg, flow_school, admin_user)
            if resolved_cfg.get("seed_key"):
                flows_by_seed_key[resolved_cfg["seed_key"]] = flow

        # Resolve composite_flow_seed_key references to actual flow UUIDs
        _resolve_composite_flow_refs(session, flows_by_seed_key)

        session.commit()

        if args.emit_tokens:
            for label, user in seeded_users.items():
                token = create_user_access_token(user)
                tokens[label] = {
                    "email": user.email or "",
                    "user_id": str(user.id),
                    "token": token,
                }

    print("Seeded admin UI demo data.")
    if args.emit_tokens:
        if args.tokens_format == "json":
            print(json.dumps(tokens, indent=2))
        else:
            for label, payload in tokens.items():
                print(f"\n[{label}] {payload['email']}")
                print(payload["token"])


if __name__ == "__main__":
    main()
