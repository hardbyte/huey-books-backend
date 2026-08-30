import textwrap

import stripe
from fastapi import FastAPI
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from starlette import status
from starlette.responses import RedirectResponse
from structlog import get_logger

from app.api.internal import router as internal_api_router
from app.config import get_settings
from app.logging import init_logging, init_tracing

api_docs = textwrap.dedent(
    """
# Wriveted Internal API 

Use this API to process long running tasks and react to non-time critical events.

## 🔐 Authentication

Protected by Cloud Run IAM. Callers must have `run.routes.invoke` and present
an identity token for this service.

## ⏰ Task Queue

While this is a standard REST API, the recommended use is via GCP Cloud Tasks.

"""
)

settings = get_settings()
init_logging(settings)
logger = get_logger()

logger.info("Starting Wriveted Internal API")

internal_app = FastAPI(
    title="Wriveted Internal API",
    description=api_docs,
    docs_url="/v1/docs",
    redoc_url="/v1/redoc",
)

init_tracing(internal_app, settings)

# Load the Stripe API key
stripe.api_key = settings.STRIPE_SECRET_KEY


@internal_app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    logger.warning(
        f"The client sent invalid data!: {exc}\n\n{exc.errors()}",
        request=request.url,
    )
    return await request_validation_exception_handler(request, exc)


internal_app.include_router(internal_api_router, prefix=settings.API_V1_STR)


@internal_app.get("/")
async def root():
    """
    Redirects to the OpenAPI documentation for the current version
    """
    return RedirectResponse("/v1/docs", status_code=status.HTTP_307_TEMPORARY_REDIRECT)
