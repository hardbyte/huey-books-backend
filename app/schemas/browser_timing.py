from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class BrowserTiming(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    operation: Literal["start", "interact"]
    response_to_commit_ms: float = Field(ge=0, le=60000)
    response_to_next_frame_ms: float = Field(ge=0, le=60000)
    request_duration_ms: float = Field(ge=0, le=60000)
