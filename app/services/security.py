import datetime
from typing import Any, Optional, Union

from jose import jwt
from pydantic import BaseModel, field_validator

from app.config import get_settings

ALGORITHM = "HS256"


def get_raw_payload_from_access_token(token) -> dict[str, Any]:
    settings = get_settings()
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.JWTError:
        # OAuth (RS256, kid-bearing) tokens are for the MCP only — it verifies and
        # scope-confines them in-process (app/mcp). They must NOT act as general
        # user credentials on the REST API, so reject them here rather than routing
        # them through the legacy user pipeline (which would ignore their scopes).
        try:
            header = jwt.get_unverified_header(token)
        except jwt.JWTError:
            raise
        if "kid" in header:
            raise jwt.JWTError("OAuth tokens are not accepted on the REST API")
        raise


class TokenPayload(BaseModel):
    sub: str
    iat: datetime.datetime
    exp: datetime.datetime

    @field_validator("sub")
    @classmethod
    def sub_must_start_with_wriveted(cls, v):
        if not v.startswith("wriveted") and ":" not in v:
            raise ValueError("Invalid JWT subject")
        return v.title()


def get_payload_from_access_token(token) -> TokenPayload:
    payload = get_raw_payload_from_access_token(token)
    return TokenPayload.model_validate(payload)


def create_access_token(
    subject: Union[str, Any],
    expires_delta: Optional[datetime.timedelta] = None,
    extra_claims: Optional[dict[str, str]] = None,
) -> str:
    settings = get_settings()

    if expires_delta is None:
        expires_delta = datetime.timedelta(hours=24)  # Default 24 hour expiry

    expire = datetime.datetime.now(datetime.UTC) + expires_delta

    to_encode = {
        "exp": expire,
        "iat": datetime.datetime.now(datetime.UTC),
        "sub": str(subject),
    }
    if extra_claims:
        to_encode.update(extra_claims)
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt
