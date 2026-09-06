from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app import crud
from app.api.dependencies.security import get_current_active_superuser
from app.api.dependencies.view_as import create_view_as_context
from app.db.session import get_session
from app.models import User

router = APIRouter(tags=["Authentication"])


@router.post("/auth/view-as/{user_id}")
def start_view_as(
    user_id: UUID,
    response: Response,
    actor: User = Depends(get_current_active_superuser),
    db: Session = Depends(get_session),
):
    target = crud.user.get_or_404(db, id=user_id)
    response.headers["Cache-Control"] = "no-store"
    return create_view_as_context(actor, target)
