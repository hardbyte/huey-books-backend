from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.browser_timing import BrowserTiming

CoverKind = Literal["exact", "alternative", "placeholder"]


class TimingObservation(BrowserTiming):
    event: Literal["response_timing"]
    schema_version: Literal[1]


class CoverObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event: Literal["cover_presented", "cover_disclosure_viewed"]
    schema_version: Literal[1]
    cover_kind: CoverKind


BrowserObservation = Annotated[
    TimingObservation | CoverObservation, Field(discriminator="event")
]
