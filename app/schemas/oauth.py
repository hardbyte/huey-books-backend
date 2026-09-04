from uuid import UUID

from pydantic import BaseModel


class OAuthConsentIn(BaseModel):
    client_id: str
    redirect_uri: str
    scope: str
    school_id: UUID
    code_challenge: str
    code_challenge_method: str = "S256"
    state: str | None = None
