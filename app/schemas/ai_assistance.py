from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class AIAssistance(BaseModel):
    schema_version: Literal[1] = 1
    model: str | None = Field(default=None, max_length=200)
    provider: str | None = Field(default=None, max_length=100)
    run: str | None = Field(default=None, max_length=200)
    prompt_version: str | None = Field(default=None, max_length=200)
    sources: list[HttpUrl] = Field(default_factory=list, max_length=20)
    rationale: str | None = Field(default=None, max_length=10000)
    uncertainties: str | None = Field(default=None, max_length=10000)
    generated_at: datetime | None = None

    model_config = ConfigDict(extra="forbid")
