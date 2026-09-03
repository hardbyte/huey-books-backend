"""Huey Books MCP server, mounted inside the API.

Library staff drive Huey Books from their own AI client (Claude, opencode, …);
Huey Books runs no AI itself. Tools call the same services/repositories and RBAC
as the REST API — see ``app/mcp/context.py`` for the OAuth-confined auth bridge.
"""

from __future__ import annotations

from fastapi import BackgroundTasks
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import Icon
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from app.api.common.pagination import PaginatedQueryParams
from app.api.recommendations import get_recommendations_with_fallback
from app.config import get_settings
from app.db.session import get_session_maker
from app.mcp._logo import LOGO_DATA_URI
from app.mcp.context import mcp_context, require_write_scope
from app.mcp.vocabulary import vocabulary
from app.models.collection import Collection
from app.repositories.labelset_repository import labelset_repository
from app.repositories.school_repository import school_repository
from app.repositories.work_repository import work_repository
from app.schemas.labelset import LabelSetCreateIn
from app.schemas.recommendations import HueyRecommendationFilter
from app.services.collection_service import CollectionService
from app.services.collections import add_editions_to_collection_by_isbn
from app.services.recommendations import enqueue_debounced_mv_refresh
from app.services.search import book_search

settings = get_settings()

_READONLY = getattr(settings, "MCP_READONLY", False)


def _collection_item_brief(item) -> dict:
    """Compact view of a held item, with the cover of the exact edition held."""
    edition = item.edition
    brief = {
        "isbn": getattr(edition, "isbn", None) or item.edition_isbn,
        "title": getattr(edition, "title", None)
        or getattr(getattr(item, "work", None), "title", None),
        "cover_url": getattr(edition, "cover_url", None),
        "copies_total": item.copies_total,
    }
    return {k: v for k, v in brief.items() if v is not None}


def _build_auth():
    """OAuth 2.1 resource-server auth via FastMCP's OAuthProxy.

    The proxy presents the DCR/PKCE/consent surface to MCP clients and delegates
    to this same service's authorization-code endpoints; it verifies the RS256
    API tokens we issue against our own JWKS. Returns None when disabled (local
    dev without OAuth)."""
    if not settings.MCP_ENABLED:
        return None
    from fastmcp.server.auth import OAuthProxy
    from fastmcp.server.auth.providers.jwt import JWTVerifier

    issuer = settings.OAUTH_ISSUER
    verifier = JWTVerifier(
        jwks_uri=f"{issuer}/.well-known/jwks.json",
        issuer=issuer,
        audience=settings.OAUTH_API_AUDIENCE,
    )
    return OAuthProxy(
        upstream_authorization_endpoint=settings.MCP_AUTHORIZE_URL,
        upstream_token_endpoint=f"{issuer}{settings.API_V1_STR}/oauth/token",
        upstream_client_id=settings.OAUTH_MCP_CLIENT_ID,
        upstream_client_secret=settings.OAUTH_MCP_CLIENT_SECRET,
        token_verifier=verifier,
        base_url=settings.MCP_BASE_URL,
        forward_pkce=True,
        # Our upstream (admin school-picker + Authorize, validated by the backend
        # /oauth/authorize) IS the user consent, so skip the proxy's own screen.
        require_authorization_consent="external",
    )


mcp = FastMCP(
    name="Huey Books",
    auth=_build_auth(),
    website_url="https://hueybooks.com",
    icons=[Icon(src=LOGO_DATA_URI, mime_type="image/png")],
    instructions=(
        "Tools for a school librarian to search, label and get recommendations "
        "from their Huey Books catalogue. Call list_label_vocabulary before "
        "labelling so you use valid hue and reading-ability keys. Prefer "
        "get_recommendations for 'what should X read' questions and search_books "
        "for finding a specific title. Before a write show the librarian what you "
        "will do and get their go-ahead."
    ),
)


@mcp.tool(annotations={"readOnlyHint": True})
async def whoami() -> dict:
    """Confirm which Huey Books account and school this session is acting as."""
    async with mcp_context() as ctx:
        return {
            "name": ctx.user.name,
            "type": ctx.user.type.value if ctx.user.type else None,
            "school": {"name": ctx.school.name, "id": ctx.school_wid},
        }


@mcp.tool(annotations={"readOnlyHint": True})
async def list_label_vocabulary() -> dict:
    """List valid hues and reading-ability tiers for labelling and recommending."""
    return vocabulary()


@mcp.tool(annotations={"readOnlyHint": True})
async def search_books(query: str, limit: int = 10) -> list[dict]:
    """Search the Huey Books catalogue by title, author or keyword."""
    async with mcp_context() as ctx:
        results = await book_search(
            ctx.db,
            query_param=query,
            pagination=PaginatedQueryParams(skip=0, limit=min(limit, 25)),
            author_id=None,
        )
        return [w.model_dump(mode="json") for w in results]


@mcp.tool(annotations={"readOnlyHint": True})
async def get_recommendations(
    hues: list[str] | None = None,
    age: int | None = None,
    reading_abilities: list[str] | None = None,
    limit: int = 5,
    school_only: bool = True,
) -> dict:
    """Recommend books by hue/age/reading ability. school_only restricts to this
    school's collection. Use hue and reading-ability keys from list_label_vocabulary."""
    async with mcp_context() as ctx:
        school = None
        if school_only:
            school = await school_repository.aget_by_wriveted_id_or_404(
                db=ctx.db, wriveted_id=ctx.school_wid
            )
        data = HueyRecommendationFilter(
            hues=hues,
            age=age,
            reading_abilities=reading_abilities,
            recommendable_only=True,
            wriveted_identifier=ctx.school_wid if school_only else None,
        )
        books, _query = await get_recommendations_with_fallback(
            ctx.db,
            ctx.user,
            school,
            data=data,
            background_tasks=BackgroundTasks(),
            limit=min(limit, 20),
        )
        return {
            "count": len(books),
            "books": [b.model_dump(mode="json") for b in books],
        }


@mcp.tool(annotations={"readOnlyHint": True})
async def get_book(work_id: int) -> dict:
    """Get a book's details and its current labels (hues, age, reading ability)."""
    async with mcp_context():
        pass

    def _get() -> dict:
        from app.schemas.work import WorkDetail

        with get_session_maker()() as db:
            work = work_repository.get_or_404(db, work_id)
            return WorkDetail.model_validate(work).model_dump(mode="json")

    return await run_in_threadpool(_get)


@mcp.tool(annotations={"readOnlyHint": True})
async def get_collection(limit: int = 20, offset: int = 0) -> dict:
    """List books in this school's collection, with holding totals."""
    async with mcp_context() as ctx:
        school_uuid = ctx.school.wriveted_identifier

    def _list() -> dict:
        with get_session_maker()() as db:
            collection = db.execute(
                select(Collection).where(Collection.school_id == school_uuid)
            ).scalar_one_or_none()
            if collection is None:
                return {"error": "This school has no catalogue uploaded yet."}
            count, items = CollectionService().list_items(
                db,
                collection_id=collection.id,
                query=None,
                reader_id=None,
                read_status=None,
                skip=offset,
                limit=min(limit, 50),
            )
            return {
                "total": count,
                "items": [_collection_item_brief(i) for i in items],
            }

    return await run_in_threadpool(_list)


if not _READONLY:

    @mcp.tool(annotations={"readOnlyHint": False, "idempotentHint": True})
    async def import_books(isbns: list[str]) -> dict:
        """Add books to this school's collection by ISBN (catalogue upload). Unknown
        books are created automatically and enriched by Huey Books afterwards.
        Confirm the list with the librarian before calling."""
        if len(isbns) > 5000:
            raise ToolError("Too many ISBNs in one import (max 5000); split the list.")
        async with mcp_context() as ctx:
            require_write_scope(ctx, "books:import")
            school_uuid = ctx.school.wriveted_identifier
            school_name = ctx.school.name
            user_id = ctx.user.id

        from app.models.user import User
        from app.schemas.collection import CollectionItemCreateIn

        # add_editions_to_collection_by_isbn (like the collection endpoints) takes
        # the sync Session, so run it on one rather than the async ctx.db.
        with get_session_maker()() as db:
            collection = db.execute(
                select(Collection).where(Collection.school_id == school_uuid)
            ).scalar_one_or_none()
            if collection is None:
                # First upload for this school: start its catalogue.
                collection = Collection(name=school_name, school_id=school_uuid)
                db.add(collection)
                db.flush()
            await add_editions_to_collection_by_isbn(
                db,
                collection_data=[CollectionItemCreateIn(edition_isbn=i) for i in isbns],
                collection=collection,
                account=db.get(User, user_id),
            )
        return {
            "requested": len(isbns),
            "note": "Editions added; metadata enrichment follows shortly.",
        }

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
    async def label_book(
        work_id: int,
        primary_hue: str,
        min_age: int,
        max_age: int,
        reading_ability: str,
        summary: str | None = None,
        secondary_hue: str | None = None,
    ) -> dict:
        """Set a book's labels: primary (and optional secondary) hue, age range,
        reading-ability tier and a short summary. Overwrites existing labels, so
        confirm the proposed labelset with the librarian first. Use valid keys from
        list_label_vocabulary. Returns the updated labels."""
        async with mcp_context() as ctx:
            require_write_scope(ctx, "books:label")

        # EDUCATOR provenance: applied by school staff, not Wriveted nor a classifier.
        update = LabelSetCreateIn(
            hue_primary_key=primary_hue,
            hue_secondary_key=secondary_hue,
            min_age=min_age,
            max_age=max_age,
            reading_ability_keys=[reading_ability],
            huey_summary=summary,
            hue_origin="EDUCATOR",
            age_origin="EDUCATOR",
            reading_ability_origin="EDUCATOR",
            summary_origin="EDUCATOR",
        )

        def _label() -> dict:
            with get_session_maker()() as db:
                work = work_repository.get_or_404(db, work_id)
                labelset = labelset_repository.get_or_create(db, work, False)
                labelset = labelset_repository.patch(db, labelset, update, True)
                return labelset.get_label_dict(db)

        result = await run_in_threadpool(_label)
        # Label edits change recommendation eligibility, so refresh the MV (debounced).
        enqueue_debounced_mv_refresh()
        return result


@mcp.prompt
def research_and_label_book(title_or_isbn: str) -> str:
    """Guide the agent through researching a book and writing a good labelset."""
    return f"""You are helping a school librarian label "{title_or_isbn}" in Huey Books.

Follow this process:
1. Call `list_label_vocabulary` to load the valid hues and reading-ability tiers.
2. Find the book with `search_books` to see its details and any existing labels.
   Do not overwrite good existing labels without reason.
3. Research the book from what you know: its story, tone, themes, intended readers.
   If you are unsure of the content, say so rather than guessing.
4. Decide the labels:
   - primary_hue: the DOMINANT reading experience/feel (one only). Add a
     secondary_hue only if a second feel is clearly present.
   - min_age / max_age: the intended reader age range in years.
   - reading_ability: the single tier matching text difficulty (SPOT easiest,
     HARRY_POTTER hardest) — decoding difficulty, not maturity of themes.
   - summary: one or two warm sentences a child or parent would find helpful.
5. Show the librarian the proposed labelset and your reasoning, and WAIT for their
   go-ahead, then call `label_book`.

Be conservative: hue and reading ability are about feel and text difficulty, not a
content warning. Ask the librarian if a judgement call is genuinely ambiguous."""


@mcp.prompt
def build_reading_list(theme: str, age: int, count: int = 10) -> str:
    """Guide the agent to build a themed, age-appropriate reading list."""
    return f"""Build a reading list of about {count} books for age {age} on the theme "{theme}",
drawn from this school's Huey Books collection.

1. Call `list_label_vocabulary` to pick hues that match the mood of "{theme}".
2. Use `get_recommendations` with those hues and age={age} (school_only=true).
3. If there are too few results, broaden the hues or set school_only=false and note
   which titles are not yet in the collection.
4. Present the list grouped by reading ability, each with a one-line reason it fits."""


@mcp.prompt
def import_from_isbn_list() -> str:
    """Guide the agent to import a pasted list of ISBNs into the collection."""
    return """The librarian will paste a list of ISBNs (or you will extract them from their text).

1. Extract clean 13- or 10-digit ISBNs, discarding notes and duplicates.
2. Confirm the count with the librarian and WAIT for their go-ahead before writing.
3. Call `import_books` with the ISBN list.
4. Report how many were added and remind them that new titles are enriched with
   metadata by Huey Books shortly after import, and can then be labelled."""


# Served at the root of the MCP host (see app/main.py host routing).
http_app = mcp.http_app(path="/mcp")
