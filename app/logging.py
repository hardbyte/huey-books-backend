import asyncio
import logging
import logging.config
from contextlib import asynccontextmanager
from functools import partial
from typing import List

import structlog
import uvicorn
from google.cloud.trace_v2 import TraceServiceClient
from opentelemetry import trace
from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter
from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.psycopg2 import Psycopg2Instrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.propagate import set_global_textmap
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter

from app.config import Settings
from app.middleware.browser_timing_receipt import BrowserTimingReceiptMiddleware
from app.middleware.request_body_limit import RequestBodyLimitMiddleware
from app.middleware.request_logging import RequestLoggingMiddleware
from app.middleware.sensitive_request_context import SensitiveRequestContextMiddleware
from app.observability.privacy import redact_database_exception, redact_log_event
from app.observability.propagation import TraceContextPropagator
from app.observability.tracing import RedactingSpanProcessor, RequestSampler


class BoundedTraceClient(TraceServiceClient):
    def batch_write_spans(self, request=None, *, retry=None, timeout=2.0, metadata=()):
        return super().batch_write_spans(
            request=request, retry=retry, timeout=timeout, metadata=metadata
        )


def create_span_processor(exporter: SpanExporter) -> BatchSpanProcessor:
    return BatchSpanProcessor(
        exporter,
        max_queue_size=1024,
        max_export_batch_size=512,
        schedule_delay_millis=200,
        export_timeout_millis=5000,
    )


def init_tracing(app, settings: Settings, *, internal: bool = False):
    set_global_textmap(TraceContextPropagator())
    trace.set_tracer_provider(
        TracerProvider(sampler=RequestSampler(settings.CHAT_TRACE_SAMPLE_RATE))
    )

    if settings.ENABLE_OTEL_GOOGLE_EXPORTER:
        cloud_trace_exporter = CloudTraceSpanExporter(
            project_id=settings.GCP_PROJECT_ID,
            client=BoundedTraceClient(),
        )
        provider = trace.get_tracer_provider()
        provider.add_span_processor(
            RedactingSpanProcessor(create_span_processor(cloud_trace_exporter))
        )
        original_lifespan = app.router.lifespan_context

        @asynccontextmanager
        async def tracing_lifespan(application):
            try:
                async with original_lifespan(application) as state:
                    yield state
            finally:
                await asyncio.to_thread(provider.shutdown)

        app.router.lifespan_context = tracing_lifespan

    SQLAlchemyInstrumentor().instrument()
    HTTPXClientInstrumentor().instrument()
    app.add_middleware(
        RequestBodyLimitMiddleware,
        paths={f"{settings.API_V1_STR}/observations"},
        max_bytes=1024,
    )
    if not internal and settings.BROWSER_OBSERVATIONS_ENABLED:
        app.add_middleware(
            BrowserTimingReceiptMiddleware, secret_key=settings.SECRET_KEY
        )
    app.add_middleware(RequestLoggingMiddleware, internal=internal)
    FastAPIInstrumentor().instrument_app(app)
    app.add_middleware(SensitiveRequestContextMiddleware)

    Psycopg2Instrumentor().instrument()
    AsyncPGInstrumentor().instrument()


def add_open_telemetry_spans(_, method_name, event_dict, *, project_id: str):
    event_dict["severity"] = {"warn": "WARNING", "exception": "ERROR"}.get(
        method_name, method_name.upper()
    )
    ctx = trace.get_current_span().get_span_context()
    if not ctx.is_valid:
        return event_dict
    event_dict["logging.googleapis.com/trace"] = (
        f"projects/{project_id}/traces/{ctx.trace_id:032x}"
    )
    event_dict["logging.googleapis.com/spanId"] = f"{ctx.span_id:016x}"
    event_dict["logging.googleapis.com/trace_sampled"] = bool(ctx.trace_flags.sampled)
    return event_dict


def init_logging(settings: Settings):
    """

    Ref: https://github.com/simonw/datasette/issues/1175#issuecomment-762488336
    """

    shared_processors: List[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        partial(add_open_telemetry_spans, project_id=settings.GCP_PROJECT_ID),
        # Don't need a timestamp as cloudrun already adds one
        # structlog.processors.TimeStamper(fmt='iso'),
        structlog.processors.StackInfoRenderer(),
    ]
    shared_processors.append(redact_database_exception)
    shared_processors.append(structlog.processors.format_exc_info)
    shared_processors.append(redact_log_event)

    logconfig_dict = {
        "version": 1,
        "filters": {"already_correlated": {"()": AlreadyCorrelatedExceptionFilter}},
        "formatters": {
            "console": {
                "()": structlog.stdlib.ProcessorFormatter,
                "processor": structlog.dev.ConsoleRenderer(),
                "foreign_pre_chain": shared_processors,
            },
            "json": {
                "()": structlog.stdlib.ProcessorFormatter,
                "processor": structlog.processors.JSONRenderer(),
                "foreign_pre_chain": shared_processors,
            },
            **uvicorn.config.LOGGING_CONFIG["formatters"],
        },
        "handlers": {
            "default": {
                "level": "DEBUG",
                "class": "logging.StreamHandler",
                "formatter": "json" if settings.LOG_AS_JSON else "console",
            },
            "uvicorn.access": {
                "level": "INFO",
                "class": "logging.StreamHandler",
                "formatter": "access",
            },
            "uvicorn.default": {
                "level": "INFO",
                "class": "logging.StreamHandler",
                "formatter": "default",
            },
        },
        "loggers": {
            "": {"handlers": ["default"], "level": "INFO"},
            "sqlalchemy": {"level": settings.SQLALCHEMY_LOGGING_LEVEL},
            "app": {"level": settings.LOGGING_LEVEL},
            "app.api.auth": {"level": settings.AUTH_LOGGING_LEVEL},
            "app.api.works": {"level": settings.LOGGING_LEVEL},
            "app.crud.collection": {"level": settings.LOGGING_LEVEL},
            "app.services": {"level": settings.LOGGING_LEVEL},
            "app.services.recommendations": {"level": settings.LOGGING_LEVEL},
            "uvicorn.error": {
                "handlers": ["default"],
                "level": "INFO",
                "propagate": False,
                "filters": ["already_correlated"],
            },
            "uvicorn.access": {
                "handlers": ["default"] if settings.LOG_UVICORN_ACCESS else [],
                "level": "INFO",
                "propagate": False,
            },
        },
    }

    processors = [
        structlog.stdlib.filter_by_level,
        *shared_processors,
        structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
    ]

    logging.config.dictConfig(logconfig_dict)

    structlog.configure(
        processors=processors,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
        context_class=dict,
    )


class AlreadyCorrelatedExceptionFilter(logging.Filter):
    def filter(self, record):
        return not (
            record.exc_info and getattr(record.exc_info[1], "_huey_logged", False)
        )
