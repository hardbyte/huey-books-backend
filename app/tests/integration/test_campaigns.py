"""Integration tests for campaigns: admin CRUD + the resolution algorithm.

Covers targeting precedence (school > region > country > global), the active
date window, visibility scoping, and the optional CEL gate.
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import text
from starlette import status

from app.models.campaign import Campaign, CampaignBiasMode
from app.models.cms import ContentVisibility, FlowDefinition, FlowNode, NodeType
from app.services.campaigns import CampaignContext, resolve_campaign


@pytest.fixture(autouse=True)
async def clean_campaigns(async_session):
    """Isolate campaigns (and the flow/session data these tests create)."""
    stmt = text(
        "TRUNCATE TABLE campaigns, conversation_sessions, flow_connections, "
        "flow_nodes, flow_definitions RESTART IDENTITY CASCADE"
    )
    await async_session.execute(stmt)
    await async_session.commit()
    yield
    await async_session.execute(stmt)
    await async_session.commit()


async def _add(async_session, **kwargs) -> Campaign:
    kwargs.setdefault("visibility", ContentVisibility.WRIVETED)
    kwargs.setdefault("bias_mode", CampaignBiasMode.BOOST)
    kwargs.setdefault("is_active", True)
    campaign = Campaign(**kwargs)
    async_session.add(campaign)
    await async_session.commit()
    await async_session.refresh(campaign)
    return campaign


def _ctx(**kwargs) -> CampaignContext:
    kwargs.setdefault("now", datetime.utcnow())
    return CampaignContext(**kwargs)


# --------------------------------------------------------------------------- #
# Admin CRUD
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_campaigns_require_admin(async_client):
    resp = await async_client.get("/v1/campaigns")
    assert resp.status_code in (
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    )


@pytest.mark.asyncio
async def test_create_get_patch_delete(async_client_authenticated_as_wriveted_user):
    client = async_client_authenticated_as_wriveted_user
    create = await client.post(
        "/v1/campaigns",
        json={"name": "Test Campaign", "country_codes": ["NZL"], "priority": 5},
    )
    assert create.status_code == status.HTTP_201_CREATED, create.text
    cid = create.json()["id"]

    got = await client.get(f"/v1/campaigns/{cid}")
    assert got.status_code == 200
    assert got.json()["country_codes"] == ["NZL"]

    patched = await client.patch(f"/v1/campaigns/{cid}", json={"priority": 9})
    assert patched.status_code == 200
    assert patched.json()["priority"] == 9

    deleted = await client.delete(f"/v1/campaigns/{cid}")
    assert deleted.status_code == status.HTTP_204_NO_CONTENT
    assert (await client.get(f"/v1/campaigns/{cid}")).status_code == 404


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_specificity_school_beats_country(async_session):
    await _add(async_session, name="NZ national", country_codes=["NZL"])
    await _add(async_session, name="School special", school_ids=[424242])

    resolved = await resolve_campaign(
        async_session, _ctx(school_id=424242, country_code="NZL")
    )
    assert resolved is not None and resolved.name == "School special"

    # A different school falls through to the national campaign.
    resolved_other = await resolve_campaign(
        async_session, _ctx(school_id=999999, country_code="NZL")
    )
    assert resolved_other is not None and resolved_other.name == "NZ national"


@pytest.mark.asyncio
async def test_priority_breaks_ties(async_session):
    await _add(async_session, name="low", country_codes=["NZL"], priority=1)
    await _add(async_session, name="high", country_codes=["NZL"], priority=10)
    resolved = await resolve_campaign(async_session, _ctx(country_code="NZL"))
    assert resolved is not None and resolved.name == "high"


@pytest.mark.asyncio
async def test_active_window_respected(async_session):
    now = datetime.utcnow()
    await _add(
        async_session,
        name="future",
        country_codes=["NZL"],
        active_from=now + timedelta(days=5),
    )
    resolved = await resolve_campaign(async_session, _ctx(country_code="NZL", now=now))
    assert resolved is None

    await _add(
        async_session,
        name="current",
        country_codes=["NZL"],
        active_from=now - timedelta(days=1),
        active_until=now + timedelta(days=1),
    )
    resolved2 = await resolve_campaign(async_session, _ctx(country_code="NZL", now=now))
    assert resolved2 is not None and resolved2.name == "current"


@pytest.mark.asyncio
async def test_visibility_school_scoped(async_session, test_school):
    await _add(
        async_session,
        name="school-only",
        visibility=ContentVisibility.SCHOOL,
        school_id=test_school.id,
    )
    # Visible to its own school...
    assert (
        await resolve_campaign(async_session, _ctx(school_id=test_school.id))
    ).name == "school-only"
    # ...but not to another school.
    assert await resolve_campaign(async_session, _ctx(school_id=999999)) is None


@pytest.mark.asyncio
async def test_cel_gate(async_session):
    await _add(
        async_session,
        name="teens-only",
        country_codes=["NZL"],
        targeting_cel="user.age >= 13",
    )
    assert (
        await resolve_campaign(async_session, _ctx(country_code="NZL", age=15))
    ).name == "teens-only"
    assert (
        await resolve_campaign(async_session, _ctx(country_code="NZL", age=8))
    ) is None


@pytest.mark.asyncio
async def test_no_match_returns_none(async_session):
    await _add(async_session, name="aus-only", country_codes=["AUS"])
    assert await resolve_campaign(async_session, _ctx(country_code="NZL")) is None


# --------------------------------------------------------------------------- #
# Resolution wired into /chat/start
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_start_without_flow_resolves_campaign(async_client, async_session):
    """No flow_id supplied → server resolves the global campaign and runs its flow."""
    flow = FlowDefinition(
        name="Campaign Flow",
        version="1",
        flow_data={"nodes": [], "connections": []},
        entry_node_id="welcome",
        is_published=True,
        is_active=True,
    )
    async_session.add(flow)
    await async_session.flush()
    async_session.add(
        FlowNode(
            flow_id=flow.id,
            node_id="welcome",
            node_type=NodeType.MESSAGE,
            content={"text": "Kia ora!"},
            position={"x": 0, "y": 0},
            info={},
        )
    )
    await async_session.commit()

    await _add(async_session, name="global-default", flow_id=flow.id)
    resp = await async_client.post("/v1/chat/start", json={})
    assert resp.status_code == status.HTTP_201_CREATED, resp.text
    assert resp.json()["flow_name"] == "Campaign Flow"


@pytest.mark.asyncio
async def test_start_without_flow_or_campaign_is_400(async_client):
    """No flow_id and nothing resolves → explicit 400 (not a 500/422)."""
    resp = await async_client.post("/v1/chat/start", json={})
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
