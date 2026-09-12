from fastapi import APIRouter, Depends, Header, HTTPException, Response

from app.config import get_settings
from app.schemas.browser_observations import BrowserObservation
from app.services.browser_observations import (
    InvalidObservationReceipt,
    browser_observation_service,
)

router = APIRouter(tags=["Observations"])


@router.post("/observations", status_code=204)
async def record_browser_observation(
    observation: BrowserObservation,
    receipt: str = Header(alias="X-Observation-Receipt", max_length=512),
    settings=Depends(get_settings),
) -> Response:
    if not settings.BROWSER_OBSERVATIONS_ENABLED:
        return Response(status_code=204)
    try:
        browser_observation_service.record(observation, receipt, settings.SECRET_KEY)
    except InvalidObservationReceipt as exc:
        raise HTTPException(
            status_code=401, detail="Invalid or expired observation receipt"
        ) from exc
    return Response(status_code=204)
