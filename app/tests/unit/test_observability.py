import json
import logging
import threading
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog
from opentelemetry import trace
from opentelemetry.propagate import get_global_textmap, set_global_textmap
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags

from app import logging as app_logging
from app.config import Settings


@pytest.fixture(autouse=True)
def restore_logging_state():
    configuration = structlog.get_config()
    propagator = get_global_textmap()
    loggers = [logging.getLogger()] + [
        logger
        for logger in logging.Logger.manager.loggerDict.values()
        if isinstance(logger, logging.Logger)
    ]
    states = [
        (logger, logger.handlers[:], logger.level, logger.disabled, logger.propagate)
        for logger in loggers
    ]
    yield
    structlog.configure(**configuration)
    set_global_textmap(propagator)
    for logger, handlers, level, disabled, propagate in states:
        logger.handlers = handlers
        logger.setLevel(level)
        logger.disabled = disabled
        logger.propagate = propagate


def test_cloud_trace_rpc_has_a_deadline_and_no_retry(monkeypatch):
    call = MagicMock()
    monkeypatch.setattr(app_logging.TraceServiceClient, "batch_write_spans", call)
    client = object.__new__(app_logging.BoundedTraceClient)
    client.batch_write_spans(request="test")
    call.assert_called_once_with(request="test", retry=None, timeout=2.0, metadata=())


@pytest.mark.asyncio
async def test_lifespan_drains_exporter_off_event_loop(monkeypatch):
    from fastapi import FastAPI

    provider = MagicMock()
    thread_id = threading.get_ident()
    provider.shutdown.side_effect = lambda: (
        pytest.fail("shutdown blocked event loop")
        if threading.get_ident() == thread_id
        else None
    )
    monkeypatch.setattr(app_logging.trace, "set_tracer_provider", lambda provider: None)
    monkeypatch.setattr(app_logging.trace, "get_tracer_provider", lambda: provider)
    monkeypatch.setattr(app_logging, "BoundedTraceClient", MagicMock())
    monkeypatch.setattr(app_logging, "CloudTraceSpanExporter", MagicMock())
    monkeypatch.setattr(app_logging, "create_span_processor", MagicMock())
    for name in [
        "HTTPXClientInstrumentor",
        "FastAPIInstrumentor",
        "Psycopg2Instrumentor",
        "AsyncPGInstrumentor",
    ]:
        monkeypatch.setattr(app_logging, name, MagicMock())
    app = FastAPI()
    settings = Settings(
        POSTGRESQL_PASSWORD="unused",
        SHOPIFY_HMAC_SECRET="unused",
        SECRET_KEY="unused",
        ENABLE_OTEL_GOOGLE_EXPORTER=True,
    )
    app_logging.init_tracing(app, settings)
    async with app.router.lifespan_context(app):
        provider.shutdown.assert_not_called()
    provider.shutdown.assert_called_once()


def test_export_does_not_block_span_end():
    entered = threading.Event()
    release = threading.Event()
    ended = threading.Event()

    class BlockingExporter(SpanExporter):
        def export(self, spans):
            entered.set()
            release.wait(3)
            return SpanExportResult.SUCCESS

    provider = TracerProvider()
    provider.add_span_processor(app_logging.create_span_processor(BlockingExporter()))

    def emit():
        with provider.get_tracer(__name__).start_as_current_span("connect"):
            pass
        ended.set()

    worker = threading.Thread(target=emit)
    worker.start()
    try:
        assert entered.wait(2)
        assert ended.is_set(), "Span.end blocked on network export"
    finally:
        release.set()
        worker.join()
        provider.shutdown()


@pytest.mark.parametrize("sampled", [True, False])
def test_json_logs_correlate_sampled_and_unsampled_traces(sampled, capsys):
    settings = Settings(
        POSTGRESQL_PASSWORD="unused",
        SHOPIFY_HMAC_SECRET="unused",
        SECRET_KEY="unused",
        LOG_AS_JSON=True,
    )
    app_logging.init_logging(settings)
    context = SpanContext(
        trace_id=42,
        span_id=7,
        is_remote=False,
        trace_flags=TraceFlags(1 if sampled else 0),
    )
    with trace.use_span(NonRecordingSpan(context)):
        structlog.get_logger("app.test").info("structured signal")
        logging.getLogger("app.test").warning("standard signal")
    records = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    assert len(records) == 2
    for record in records:
        assert (
            record["logging.googleapis.com/trace"]
            == f"projects/wriveted-api/traces/{42:032x}"
        )
        assert record["logging.googleapis.com/spanId"] == f"{7:016x}"
        assert record["logging.googleapis.com/trace_sampled"] is sampled
    assert [record["severity"] for record in records] == ["INFO", "WARNING"]


def test_no_trace_fields_outside_request():
    result = app_logging.add_open_telemetry_spans(None, "info", {}, project_id="test")
    assert "logging.googleapis.com/trace" not in result


@pytest.mark.asyncio
async def test_request_summary_uses_route_template_not_secrets(monkeypatch):
    from app.middleware.request_logging import RequestLoggingMiddleware

    messages = []
    log = []
    monkeypatch.setattr(
        "app.middleware.request_logging.logger.info", lambda event, **fields: log.append(fields)
    )

    async def app(scope, receive, send):
        from types import SimpleNamespace

        scope["route"] = SimpleNamespace(
            path="/v1/chat/sessions/{session_token}/interact"
        )
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def send(message):
        messages.append(message)

    await RequestLoggingMiddleware(app)(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/chat/sessions/secret/interact",
            "query_string": b"answer=private",
        },
        AsyncMock(),
        send,
    )
    assert len(log) == 1
    assert log[0]["route"] == "/v1/chat/sessions/{session_token}/interact"
    assert "secret" not in json.dumps(log) and "private" not in json.dumps(log)
    assert log[0]["status_code"] == 200
    assert (
        dict(messages[0]["headers"])[b"x-request-id"].decode() == log[0]["request_id"]
    )
