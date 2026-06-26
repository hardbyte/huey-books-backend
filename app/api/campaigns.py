from typing import Optional, Union
from uuid import UUID

from fastapi import APIRouter, Query, Security
from sqlalchemy import select
from starlette import status
from starlette.exceptions import HTTPException
from structlog import get_logger

from app.api.dependencies.async_db_dep import DBSessionDep
from app.api.dependencies.security import (
    get_current_active_superuser_or_backend_service_account,
)
from app.models import ServiceAccount, User
from app.models.campaign import Campaign
from app.schemas.campaign import (
    CampaignBrief,
    CampaignCreateIn,
    CampaignDetail,
    CampaignUpdateIn,
)

logger = get_logger()

router = APIRouter(
    tags=["Campaigns"],
    dependencies=[Security(get_current_active_superuser_or_backend_service_account)],
)


async def _get_or_404(session: DBSessionDep, campaign_id: UUID) -> Campaign:
    campaign = await session.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return campaign


@router.get("/campaigns", response_model=list[CampaignBrief])
async def list_campaigns(
    session: DBSessionDep,
    is_active: Optional[bool] = Query(None),
    school_id: Optional[int] = Query(None),
):
    query = select(Campaign).order_by(
        Campaign.priority.desc(), Campaign.created_at.desc()
    )
    if is_active is not None:
        query = query.where(Campaign.is_active.is_(is_active))
    if school_id is not None:
        query = query.where(Campaign.school_id == school_id)
    rows = (await session.execute(query)).scalars().all()
    return [CampaignBrief.model_validate(c) for c in rows]


@router.post(
    "/campaigns", response_model=CampaignDetail, status_code=status.HTTP_201_CREATED
)
async def create_campaign(
    data: CampaignCreateIn,
    session: DBSessionDep,
    actor: Union[User, ServiceAccount] = Security(
        get_current_active_superuser_or_backend_service_account
    ),
):
    campaign = Campaign(**data.model_dump())
    if isinstance(actor, User):
        campaign.created_by = actor.id
    session.add(campaign)
    await session.commit()
    await session.refresh(campaign)
    logger.info("Created campaign", campaign_id=str(campaign.id), name=campaign.name)
    return CampaignDetail.model_validate(campaign)


@router.get("/campaigns/{campaign_id}", response_model=CampaignDetail)
async def get_campaign(campaign_id: UUID, session: DBSessionDep):
    return CampaignDetail.model_validate(await _get_or_404(session, campaign_id))


@router.patch("/campaigns/{campaign_id}", response_model=CampaignDetail)
async def update_campaign(
    campaign_id: UUID, data: CampaignUpdateIn, session: DBSessionDep
):
    campaign = await _get_or_404(session, campaign_id)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(campaign, field, value)
    await session.commit()
    await session.refresh(campaign)
    logger.info("Updated campaign", campaign_id=str(campaign.id))
    return CampaignDetail.model_validate(campaign)


@router.delete("/campaigns/{campaign_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_campaign(campaign_id: UUID, session: DBSessionDep):
    campaign = await _get_or_404(session, campaign_id)
    await session.delete(campaign)
    await session.commit()
    logger.info("Deleted campaign", campaign_id=str(campaign_id))
