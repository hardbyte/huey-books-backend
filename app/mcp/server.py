"""Huey Books MCP server, mounted inside the API.

Library staff drive Huey Books from their own AI client (Claude, opencode, …);
Huey Books runs no AI itself. Tools call the same services/repositories and RBAC
as the REST API — see ``app/mcp/context.py`` for the OAuth-confined auth bridge.
"""

from __future__ import annotations

from fastapi import BackgroundTasks
from fastmcp import FastMCP
from mcp.types import Icon

from app.api.common.pagination import PaginatedQueryParams
from app.api.recommendations import get_recommendations_with_fallback
from app.config import get_settings
from app.mcp._logo import LOGO_DATA_URI
from app.mcp.context import mcp_context
from app.mcp.vocabulary import vocabulary
from app.repositories.school_repository import school_repository
from app.schemas.recommendations import HueyRecommendationFilter
from app.services.search import book_search

settings = get_settings()


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


# ASGI app to mount on the FastAPI application (see app/main.py).
http_app = mcp.http_app(path="/")
