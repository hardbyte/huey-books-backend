from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.school import SchoolKind
from app.models.user import User, UserAccountType
from app.repositories import people as repository
from app.repositories.event_repository import event_repository
from app.repositories.organisation_repository import organisation_repository
from app.schemas.people import PeoplePage, Person, PersonAccess
from app.services.email_notification import EmailType, send_email_reliable
from app.services.organisation_entitlements import resolve_organisation_entitlements
from app.services.organisation_management import require_library_manager_retained
from app.services.organisation_workspace import (
    ELIGIBLE_ROLES,
    LibraryAccess,
    is_platform_staff,
    manages_organisation,
    require_organisation_manager,
    resolve_library,
)
from app.services.school_emails import render_school_staff_invite_html
from app.services.workspace_errors import (
    WorkspaceConflict,
    WorkspaceForbidden,
    WorkspaceInvalid,
    WorkspaceNotFound,
)


@dataclass
class PeopleContext:
    name: str
    organisation_id: UUID | None
    library: LibraryAccess | None
    school_management: bool
    can_invite: bool

    @property
    def school_id(self):
        return self.library.library.id if self.library else None

    @property
    def roles(self):
        if not self.can_invite:
            return []
        if self.library is None:
            return ["manager"]
        return ["reviewer", "cataloguer", "manager"] + (
            ["educator", "school_admin"] if self.school_management else []
        )


async def context(
    db: AsyncSession, actor: User, scope: str, scope_id: UUID, *, lock: bool = True
) -> PeopleContext:
    if scope == "organisations":
        organisation = await require_organisation_manager(
            db, actor, scope_id, lock=lock
        )
        entitlement = (await resolve_organisation_entitlements(db, {scope_id}))[
            scope_id
        ]
        return PeopleContext(
            organisation.name,
            scope_id,
            None,
            False,
            entitlement.library_count <= 1 or entitlement.multiple_libraries,
        )
    await organisation_repository.get_library(db, scope_id, lock=lock)
    if lock:
        actor = await repository.actor_account(db, actor.id)
        if actor is None or not actor.is_active:
            raise WorkspaceForbidden("Your account no longer has access")
    access = await resolve_library(db, actor, scope_id, "revoke_members")
    # Home roles (educator, school administrator) are education-unit facts, so
    # they are only offered where the row is one. Library rows reject them in
    # the database as well; see app/db/triggers.py.
    school_management = access.library.kind is SchoolKind.SCHOOL and (
        is_platform_staff(actor)
        or (
            actor.type == UserAccountType.SCHOOL_ADMIN
            and getattr(actor, "school_id", None) == access.library.id
        )
    )
    return PeopleContext(
        access.library.name,
        access.library.organisation_id,
        access,
        school_management,
        "manage_members" in access.capabilities,
    )


async def list_people(
    db: AsyncSession, actor: User, scope: str, scope_id: UUID, q="", skip=0, limit=25
):
    ctx = await context(db, actor, scope, scope_id, lock=False)
    rows, total, direct, inherited = await repository.page(
        db, ctx.school_id, ctx.organisation_id, q, skip, limit
    )
    can_manage_org = bool(
        ctx.organisation_id
        and await manages_organisation(db, actor, ctx.organisation_id)
    )
    data = []
    for row in rows:
        access = []
        if ctx.library and row["school_id"] == ctx.school_id:
            access.append(
                PersonAccess(
                    source="home",
                    role=row["type"].value,
                    can_edit=ctx.school_management,
                    can_remove=ctx.school_management,
                )
            )
        if row["id"] in direct:
            access.append(
                PersonAccess(
                    source="direct",
                    role=direct[row["id"]],
                    can_edit=ctx.can_invite,
                    can_remove=True,
                )
            )
        if row["id"] in inherited:
            access.append(
                PersonAccess(
                    source="organisation",
                    role="manager",
                    can_edit=False,
                    can_remove=ctx.library is None,
                    manage_url=f"/people/?scope=organisations&id={ctx.organisation_id}"
                    if ctx.library and can_manage_org
                    else None,
                )
            )
        data.append(
            Person(
                user_id=row["id"],
                name=row["name"],
                email=row["email"],
                is_active=row["is_active"],
                last_login_at=row["last_login_at"],
                access=access,
            )
        )
    return PeoplePage(
        name=ctx.name,
        data=data,
        total=total,
        available_roles=ctx.roles,
        can_invite=ctx.can_invite,
    )


async def notify(db, ctx, target):
    settings = get_settings()
    await send_email_reliable(
        db,
        {
            "from_email": settings.BROADCAST_FROM_EMAIL,
            "from_name": "Huey Books",
            "to_emails": [target["email"]],
            "subject": f"Your access to {ctx.name} on Huey Books",
            "html_content": render_school_staff_invite_html(
                ctx.name, target["name"], settings.SCHOOL_ADMIN_URL
            ),
        },
        email_type=EmailType.ONBOARDING,
        user_id=str(target["id"]),
    )


async def audit(db, actor, ctx, user_id, action, source, previous_role=None, role=None):
    await event_repository.acreate(
        db,
        title=f"People access {action}",
        account=actor,
        school=ctx.library.library if ctx.library else None,
        commit=False,
        info={
            "target_user_id": str(user_id),
            "organisation_id": str(ctx.organisation_id)
            if ctx.organisation_id
            else None,
            "source": source,
            "previous_role": previous_role,
            "role": role,
        },
    )


async def add_person(db: AsyncSession, actor: User, scope: str, scope_id: UUID, data):
    ctx = await context(db, actor, scope, scope_id)
    if data.role not in ctx.roles:
        raise WorkspaceForbidden("You cannot grant this role here")
    await repository.lock_email(db, str(data.email))
    matches = await repository.find_email(db, str(data.email))
    if len(matches) > 1:
        raise WorkspaceConflict(
            "This email matches multiple accounts. Ask Huey Books staff to resolve it."
        )
    user_id = (
        matches[0]
        if matches
        else await repository.create_person(db, data.name, str(data.email))
    )
    target = await repository.person(db, user_id)
    if not target["is_active"] or target["type"] not in ELIGIBLE_ROLES:
        raise WorkspaceConflict(
            "This account cannot be added here. Contact Huey Books staff."
        )
    if data.role in ("educator", "school_admin"):
        if target["school_id"] is not None:
            raise WorkspaceConflict(
                "This person already has a school assignment. Edit their existing school role, or add library access without moving them."
            )
        await repository.set_home(db, user_id, ctx.school_id, data.role)
        source = "home"
    elif scope == "organisations":
        if await organisation_repository.organisation_membership(db, scope_id, user_id):
            raise WorkspaceConflict("This person already has organisation access")
        await organisation_repository.grant_organisation_membership(
            db, scope_id, user_id
        )
        source = "organisation"
    else:
        if await organisation_repository.library_membership(db, ctx.school_id, user_id):
            raise WorkspaceConflict(
                "This person already has direct library access. Use Change role."
            )
        await organisation_repository.grant_library_membership(
            db, ctx.school_id, user_id, data.role
        )
        source = "direct"
    await notify(db, ctx, target)
    await audit(db, actor, ctx, user_id, "added", source, role=data.role)
    await db.commit()


async def source_role(db, ctx, target, source):
    if source == "home" and ctx.library and target["school_id"] == ctx.school_id:
        if not ctx.school_management:
            raise WorkspaceForbidden(
                "Only this school's administrators or Huey Books staff can manage school roles"
            )
        return target["type"].value
    if source == "direct" and ctx.library:
        member = await organisation_repository.library_membership(
            db, ctx.school_id, target["id"]
        )
        if member:
            return member.role
    if source == "organisation" and ctx.library is None:
        if await organisation_repository.organisation_membership(
            db, ctx.organisation_id, target["id"]
        ):
            return "manager"
    raise WorkspaceNotFound(
        "This access no longer exists here. Refresh the people list."
    )


async def change_person(
    db, actor, scope, scope_id, user_id, source, expected_role, role=None
):
    ctx = await context(db, actor, scope, scope_id)
    target = await repository.person(db, user_id)
    if target is None:
        raise WorkspaceNotFound("Person not found")
    previous = await source_role(db, ctx, target, source)
    if previous != expected_role:
        raise WorkspaceConflict("Their role changed. Refresh before trying again.")
    if role is not None and role not in ctx.roles:
        raise WorkspaceForbidden("You cannot grant this role here")
    if source == "home":
        if role not in (None, "educator", "school_admin"):
            raise WorkspaceInvalid("School roles are educator or school administrator")
        if (
            previous == "school_admin"
            and role != previous
            and target["is_active"]
            and await repository.active_home_admin_count(db, ctx.school_id) <= 1
        ):
            raise WorkspaceConflict(
                "Add another school administrator before removing or changing the last administrator"
            )
        await repository.set_home(
            db, user_id, ctx.school_id if role else None, role or "educator"
        )
    elif source == "direct":
        if role not in (None, "manager", "reviewer", "cataloguer"):
            raise WorkspaceInvalid("Choose a library role")
        if role != "manager":
            await require_library_manager_retained(db, actor, ctx.library, user_id)
        if role:
            await organisation_repository.grant_library_membership(
                db, ctx.school_id, user_id, role
            )
        else:
            await db.delete(
                await organisation_repository.library_membership(
                    db, ctx.school_id, user_id
                )
            )
    else:
        if role is not None:
            raise WorkspaceInvalid("Organisation managers have one role")
        if (
            target["is_active"]
            and target["type"] in ELIGIBLE_ROLES
            and await organisation_repository.active_organisation_manager_count(
                db, scope_id, ELIGIBLE_ROLES
            )
            <= 1
        ):
            raise WorkspaceConflict("Keep at least one organisation manager")
        await db.delete(
            await organisation_repository.organisation_membership(db, scope_id, user_id)
        )
    await audit(
        db,
        actor,
        ctx,
        user_id,
        "changed" if role else "removed",
        source,
        previous,
        role,
    )
    await db.commit()


async def resend(db, actor, scope, scope_id, user_id):
    ctx = await context(db, actor, scope, scope_id)
    if not ctx.can_invite:
        raise WorkspaceForbidden("You cannot send invitations here")
    target = await repository.person(db, user_id, lock=False)
    if not target or not target["email"] or not target["is_active"]:
        raise WorkspaceNotFound("Active person not found")
    email = target["email"]
    await repository.lock_email(db, email)
    target = await repository.person(db, user_id)
    if not target or not target["is_active"] or target["email"] != email:
        raise WorkspaceConflict(
            "Account changed. Refresh before sending another email."
        )
    direct = ctx.library and await organisation_repository.library_membership(
        db, ctx.school_id, user_id
    )
    home = ctx.school_management and target["school_id"] == ctx.school_id
    organisation = (
        ctx.library is None
        and await organisation_repository.organisation_membership(db, scope_id, user_id)
    )
    if not (direct or home or organisation):
        raise WorkspaceForbidden("Manage this person's invitation at its access source")
    if await repository.recently_emailed(db, user_id):
        raise WorkspaceConflict(
            "An email was queued recently. Please wait five minutes before resending."
        )
    await notify(db, ctx, target)
    await audit(
        db,
        actor,
        ctx,
        user_id,
        "email queued",
        "organisation" if organisation else "home" if home else "direct",
    )
    await db.commit()
