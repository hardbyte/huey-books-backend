import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.search import update_search_view_v1


async def test_search_refresh_function_security(async_session: AsyncSession):
    result = await async_session.execute(
        text("""
            SELECT p.prosecdef, p.proconfig,
                   has_function_privilege('cloudrun', p.oid, 'EXECUTE'),
                   has_function_privilege('readonly', p.oid, 'EXECUTE'),
                   pg_get_userbyid(p.proowner) = pg_get_userbyid(c.relowner)
            FROM pg_proc p
            JOIN pg_class c ON c.oid = 'public.search_view_v1'::regclass
            WHERE p.oid = 'public.refresh_search_index()'::regprocedure
        """)
    )
    assert result.one() == (
        True,
        ["search_path=pg_catalog, pg_temp"],
        True,
        False,
        True,
    )


async def test_search_refresh_as_runtime_role(async_session: AsyncSession):
    await async_session.execute(text("SET LOCAL ROLE cloudrun"))
    assert (
        await async_session.execute(text("SELECT current_user"))
    ).scalar() == "cloudrun"
    await update_search_view_v1(async_session)


async def test_search_refresh_stays_unavailable_to_readonly(
    async_session: AsyncSession,
):
    await async_session.execute(text("SET LOCAL ROLE readonly"))
    with pytest.raises(DBAPIError, match="permission denied"):
        await update_search_view_v1(async_session)
    await async_session.rollback()


async def test_runtime_cannot_refresh_materialized_views_directly(
    async_session: AsyncSession,
):
    await async_session.execute(text("SET LOCAL ROLE cloudrun"))
    with pytest.raises(DBAPIError, match="permission denied"):
        await async_session.execute(
            text("REFRESH MATERIALIZED VIEW public.search_view_v1")
        )
    await async_session.rollback()
