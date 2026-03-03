from typing import Any, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from pydantic_settings import BaseSettings
from sendgrid import SendGridAPIClient
from sqlalchemy.orm import Session
from structlog import get_logger
from twilio.rest import Client as TwilioClient

from app import crud
from app.api.dependencies.async_db_dep import DBSessionDep

# Import tasks router
from app.api.internal.tasks import router as tasks_router
from app.db.session import get_session
from app.models.event import EventSlackChannel
from app.repositories.service_account_repository import service_account_repository
from app.repositories.work_repository import work_repository
from app.schemas.feedback import SendEmailPayload, SendSmsPayload
from app.schemas.users.huey_attributes import HueyAttributes
from app.services import search
from app.services.booklists import generate_reading_pathway_lists
from app.services.commerce import (
    get_sendgrid_api,
    get_twilio_client,
    send_sendgrid_email,
)
from app.services.events import handle_event_to_slack_alert, process_events
from app.services.hydration import hydrate_bulk
from app.services.labelling import label_and_update_work
from app.services.stripe_events import process_stripe_event


class CloudRunEnvironment(BaseSettings):
    K_SERVICE: str | None = None
    K_REVISION: str | None = None
    K_CONFIGURATION: str | None = None


cloud_run_config = CloudRunEnvironment()

logger = get_logger()

router = APIRouter()

# Include tasks sub-router
router.include_router(tasks_router)


@router.get("/version")
async def get_version():
    cloud_run_revision = cloud_run_config.K_REVISION or "Unknown"
    return {"cloud_run_revision": cloud_run_revision, "version": "internal"}


class ProcessEventPayload(BaseModel):
    event_id: str


@router.post("/process-event")
def process_event(data: ProcessEventPayload):
    """
    Legacy endpoint for direct event processing.

    Note: This endpoint is maintained for backward compatibility but new
    events will be processed via the EventOutbox system for better reliability.
    """
    return process_events(
        event_id=data.event_id,
    )


@router.post("/process-outbox-events")
async def process_outbox_events(session: DBSessionDep):
    """
    Process pending events from the EventOutbox.

    This is the new unified background processor that handles all reliable
    event delivery including Slack alerts, webhooks, and internal processing.
    """
    from app.services.event_outbox_service import EventOutboxService

    outbox_service = EventOutboxService()
    stats = await outbox_service.process_pending_events(session)

    logger.info("EventOutbox processing completed", stats=stats)
    return {"msg": "EventOutbox processing completed", "stats": stats}


class EventSlackAlertPayload(BaseModel):
    event_id: str
    slack_channel: EventSlackChannel = EventSlackChannel.GENERAL
    slack_extra: dict[str, str] | None = None


@router.post("/event-to-slack-alert")
def event_to_slack_alert(
    data: EventSlackAlertPayload,
    session: Session = Depends(get_session),
):
    """
    Legacy endpoint for direct Slack alerts.

    Note: This endpoint is now deprecated. New code should use the unified
    create_event workflow with slack_channel parameter, which automatically
    queues alerts via the EventOutbox for better reliability.
    """
    logger.warning(
        "Using deprecated direct Slack alert endpoint. Consider using unified event workflow.",
        event_id=data.event_id,
    )
    handle_event_to_slack_alert(
        session, data.event_id, data.slack_channel, extra=data.slack_extra
    )


class StripeInternalEventPayload(BaseModel):
    stripe_event_type: str
    stripe_event_data: Any = None


@router.post("/process-stripe-event")
def handle_stripe_event(data: StripeInternalEventPayload):
    logger.info("Internal API processing a stripe event", data=data)

    return process_stripe_event(
        event_type=data.stripe_event_type,
        event_data=data.stripe_event_data,
    )


class GenerateReadingPathwaysPayload(BaseModel):
    user_id: str
    attributes: HueyAttributes
    limit: int = 10


@router.post("/generate-reading-pathways")
async def handle_generate_reading_pathways(data: GenerateReadingPathwaysPayload):
    logger.info(
        "Internal API starting generating reading pathways", user_id=data.user_id
    )
    await generate_reading_pathway_lists(
        user_id=data.user_id,
        attributes=data.attributes,
        limit=data.limit,
        commit=True,
    )

    logger.info("Finished generating reading pathways", user_id=data.user_id)

    return {"msg": "ok"}


class GenerateLabelsPayload(BaseModel):
    work_id: int


@router.post("/generate-labels")
async def handle_generate_labels(
    data: GenerateLabelsPayload,
    session: Session = Depends(get_session),
):
    logger.info("Internal API generating labels for work", work_id=data.work_id)
    work = work_repository.get_or_404(db=session, id=data.work_id)
    try:
        await label_and_update_work(work, session)
        logger.info(
            "Labels generated",
        )
    except Exception as e:
        logger.error("Error generating labels. Ignoring.", exc_info=e)
        return {"msg": "error"}
    return {"msg": "ok"}


# Maintenance: Regenerate flow_data snapshots
class RegenerateFlowsPayload(BaseModel):
    flow_ids: Optional[List[UUID]] = None


@router.post("/flows/regenerate-snapshots")
async def regenerate_flow_snapshots(
    session: DBSessionDep,
    payload: RegenerateFlowsPayload | None = None,
):
    """
    Rebuild `flow_definitions.flow_data` from canonical tables for all (or specified) flows.
    """
    from app.services.flow_service import FlowService

    service = FlowService()
    ids = payload.flow_ids if payload else None
    stats = await service.regenerate_all_flow_data(session, ids)
    logger.info("Regenerated flow snapshots", stats=stats)
    return {"msg": "ok", "stats": stats}


@router.post("/send-email")
def handle_send_email(
    data: SendEmailPayload,
    session: Session = Depends(get_session),
    sg: SendGridAPIClient = Depends(get_sendgrid_api),
):
    logger.info("Internal API sending emails")
    user_account = crud.user.get(db=session, id=data.user_id)
    svc_account = service_account_repository.get(db=session, id=data.service_account_id)
    account = user_account or svc_account
    send_sendgrid_email(data.email_data, session, sg, account=account)


@router.post("/send-sms")
def handle_send_sms(
    data: SendSmsPayload,
    client: TwilioClient = Depends(get_twilio_client),
):
    logger.info("Internal API sending sms", data=data)
    return client.messages.create(**data)


@router.post("/hydrate-bulk")
async def handle_hydrate_bulk(
    isbns: list[str], session: Session = Depends(get_session)
):
    logger.info(f"Internal API hydrating {len(isbns)} isbns")
    await hydrate_bulk(session, isbns)

    return {"msg": "ok"}


@router.post("/update-search-index")
async def handle_update_search_index(session: DBSessionDep):
    logger.info("Internal API updating search index")
    await search.update_search_view_v1(session)
    logger.info("Processing search data updated event")
    return {"msg": "ok"}
