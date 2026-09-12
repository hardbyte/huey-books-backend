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
        "SQLAlchemyInstrumentor",
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


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/v1/chat/start", True),
        ("/v1/chat/session/interact", True),
        ("/v1/chat/sessions/token/interact", True),
        ("/v1/chat/admin/sessions", False),
        ("/v1/schools", False),
        ("/v1/version", False),
        ("/v1/chat/telemetry", False),
    ],
)
@pytest.mark.parametrize("path_attribute", ["http.target", "url.path"])
def test_chat_sampling_overrides_unsampled_remote_parent_only_for_chat(
    path, expected, path_attribute
):
    from app.observability.tracing import RequestSampler

    parent = trace.set_span_in_context(
        NonRecordingSpan(
            SpanContext(
                trace_id=42, span_id=7, is_remote=True, trace_flags=TraceFlags(0)
            )
        )
    )
    decision = RequestSampler(1).should_sample(
        parent, 42, "POST", trace.SpanKind.SERVER, {path_attribute: path}
    )
    assert decision.decision.is_sampled() is expected
    assert (
        not RequestSampler(0)
        .should_sample(
            parent, 42, "POST", trace.SpanKind.SERVER, {path_attribute: path}
        )
        .decision.is_sampled()
    )


@pytest.mark.parametrize("json_logging", [True, False])
def test_exception_logs_redacted_before_console_or_json_output(json_logging, capsys):
    from app.observability.privacy import request_secrets

    app_logging.init_logging(
        Settings(
            POSTGRESQL_PASSWORD="unused",
            SHOPIFY_HMAC_SECRET="unused",
            SECRET_KEY="unused",
            LOG_AS_JSON=json_logging,
        )
    )
    context = request_secrets.set(("bare-session-secret",))
    try:
        try:
            raise ValueError(
                "bare-session-secret /v1/chat/sessions/other-secret/interact"
            )
        except ValueError:
            structlog.get_logger("app.test").exception(
                "failure", user_input="child-private-answer"
            )
    finally:
        request_secrets.reset(context)
    captured = capsys.readouterr()
    assert "bare-session-secret" not in captured.err + captured.out
    assert "other-secret" not in captured.err + captured.out
    assert "child-private-answer" not in captured.err + captured.out
    assert "ValueError" in captured.err


def test_disabled_access_logging_and_application_debug_overrides(capsys):
    app_logging.init_logging(
        Settings(
            POSTGRESQL_PASSWORD="unused",
            SHOPIFY_HMAC_SECRET="unused",
            SECRET_KEY="unused",
            LOG_AS_JSON=True,
        )
    )
    logging.getLogger("uvicorn.access").info("secret-path")
    structlog.get_logger("app.services.webhook_notifier").debug(
        "No webhooks configured"
    )
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("json_logging", [True, False])
def test_database_error_logs_retain_class_and_sqlstate_not_parameter_values(
    json_logging, capsys
):
    from sqlalchemy.exc import DataError

    class OriginalDatabaseError(Exception):
        pgcode = "22P02"

    private_value = "synthetic-private-bound-value"
    app_logging.init_logging(
        Settings(
            POSTGRESQL_PASSWORD="unused",
            SHOPIFY_HMAC_SECRET="unused",
            SECRET_KEY="unused",
            LOG_AS_JSON=json_logging,
        )
    )
    original = OriginalDatabaseError(f"invalid input: {private_value}")
    try:
        raise DataError(
            "SELECT :value", {"value": private_value}, original, hide_parameters=True
        )
    except DataError as exc:
        structlog.get_logger("app.test").exception("Query failed", error=str(exc))
        logging.getLogger("app.test").exception("Standard query failed")
        structlog.get_logger("app.test").error("Explicit exception", exc_info=exc)
    captured = capsys.readouterr()
    assert private_value not in captured.err + captured.out
    assert "DataError" in captured.err
    assert "22P02" in captured.err


def test_parent_span_does_not_echo_database_error_values():
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )
    from sqlalchemy.exc import DataError

    from app.observability.tracing import RedactingSpanProcessor

    private_value = "synthetic-private-bound-value"
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(RedactingSpanProcessor(SimpleSpanProcessor(exporter)))
    try:
        with pytest.raises(DataError):
            with provider.get_tracer(__name__).start_as_current_span(
                "application request"
            ):
                raise DataError(
                    "SELECT :value",
                    {"value": private_value},
                    ValueError(private_value),
                    hide_parameters=True,
                )
        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].status.status_code == trace.StatusCode.ERROR
        assert (
            spans[0].events[0].attributes["exception.type"]
            == "sqlalchemy.exc.DataError"
        )
        assert private_value not in spans[0].to_json()
    finally:
        provider.shutdown()


@pytest.mark.parametrize(
    "database_attributes",
    [
        {"db.system": "postgresql"},
        {"db.statement": "SELECT 1"},
        {"db.system.name": "postgresql"},
        {"db.query.text": "SELECT 1"},
    ],
)
def test_database_span_exception_without_attributes_is_safe(database_attributes):
    from opentelemetry.sdk.trace import Event, ReadableSpan

    from app.observability.tracing import RedactingSpanProcessor

    processor = MagicMock()
    RedactingSpanProcessor(processor).on_end(
        ReadableSpan(
            "query", attributes=database_attributes, events=[Event("exception")]
        )
    )
    processor.on_end.assert_called_once()


@pytest.mark.parametrize("attribute", ["db.system", "db.system.name"])
def test_database_redaction_removes_driver_values_for_both_conventions(attribute):
    from opentelemetry.sdk.trace import Event, ReadableSpan

    from app.observability.tracing import RedactingSpanProcessor

    processor = MagicMock()
    RedactingSpanProcessor(processor).on_end(
        ReadableSpan(
            "query",
            attributes={attribute: "postgresql"},
            status=trace.Status(trace.StatusCode.ERROR, "private-value"),
            events=[
                Event(
                    "exception",
                    {
                        "exception.type": "DriverError",
                        "exception.message": "private-value",
                        "exception.stacktrace": "private-value",
                    },
                )
            ],
        )
    )
    exported = processor.on_end.call_args.args[0]
    assert "private-value" not in exported.to_json()
    assert exported.events[0].attributes == {"exception.type": "DriverError"}


@pytest.mark.parametrize("legacy", [True, False])
def test_full_asgi_error_correlates_once_and_redacts_spans_before_async_export(
    legacy, capsys
):
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )
    from starlette.responses import JSONResponse

    from app.middleware.request_logging import RequestLoggingMiddleware
    from app.middleware.sensitive_request_context import (
        SensitiveRequestContextMiddleware,
    )
    from app.observability.privacy import request_secrets
    from app.observability.tracing import RedactingSpanProcessor, RequestSampler

    app_logging.init_logging(
        Settings(
            POSTGRESQL_PASSWORD="unused",
            SHOPIFY_HMAC_SECRET="unused",
            SECRET_KEY="unused",
            LOG_AS_JSON=True,
        )
    )
    provider = TracerProvider(sampler=RequestSampler(1))
    exporter = InMemorySpanExporter()
    provider.add_span_processor(
        RedactingSpanProcessor(app_logging.create_span_processor(exporter))
    )
    app = FastAPI()

    @app.exception_handler(Exception)
    async def error_response(request: Request, exc: Exception):
        # The response adapter deliberately does not log a second traceback.
        return JSONResponse(
            {"detail": "Internal server error"},
            status_code=500,
            headers={"X-Request-ID": request.state.request_id},
        )

    @app.post("/v1/chat/sessions/{session_token}/interact")
    @app.post("/v1/chat/session/interact")
    def broken():
        with provider.get_tracer("test").start_as_current_span("query"):
            raise RuntimeError("sensitive-credential")

    app.add_middleware(RequestLoggingMiddleware)
    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
    app.add_middleware(SensitiveRequestContextMiddleware)
    path = (
        "/v1/chat/sessions/sensitive-credential/interact"
        if legacy
        else "/v1/chat/session/interact"
    )
    headers = {"X-Cloud-Trace-Context": f"{42:032x}/7;o=0"}
    if not legacy:
        headers["X-Chat-Session"] = "sensitive-credential"
    response = TestClient(app, raise_server_exceptions=False).post(
        path, headers=headers
    )
    assert response.status_code == 500
    assert not request_secrets.get()
    provider.force_flush()
    spans = exporter.get_finished_spans()
    assert spans
    assert "sensitive-credential" not in "".join(span.to_json() for span in spans)
    records = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    errors = [
        record
        for record in records
        if record.get("event") == "Unhandled request exception"
    ]
    assert len(errors) == 1
    assert errors[0]["request_id"] == response.headers["X-Request-ID"]
    assert errors[0]["logging.googleapis.com/trace"]
    assert "sensitive-credential" not in json.dumps(errors)
    summaries = [
        record for record in records if record.get("event") == "HTTP request completed"
    ]
    assert len(summaries) == 1
    assert summaries[0]["failed"] is True
    assert summaries[0]["severity"] == "ERROR"
    provider.shutdown()


@pytest.mark.asyncio
async def test_stream_failure_is_not_a_success_and_duplicate_uvicorn_exception_is_suppressed(
    monkeypatch,
):
    from app.middleware.request_logging import RequestLoggingMiddleware

    log = []
    monkeypatch.setattr(
        "app.middleware.request_logging.logger.error",
        lambda event, **fields: log.append(fields),
    )
    monkeypatch.setattr(
        "app.middleware.request_logging.logger.exception", lambda *args, **kwargs: None
    )

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send(
            {"type": "http.response.body", "body": b"partial", "more_body": True}
        )
        raise RuntimeError("stream failed")

    with pytest.raises(RuntimeError) as error:
        await RequestLoggingMiddleware(app)(
            {"type": "http", "method": "GET"}, AsyncMock(), AsyncMock()
        )
    assert log[0]["status_code"] == 200
    assert log[0]["failed"] is True
    assert log[0]["response_complete"] is False
    record = logging.LogRecord(
        "uvicorn.error",
        logging.ERROR,
        "",
        0,
        "ASGI exception",
        (),
        (RuntimeError, error.value, None),
    )
    assert app_logging.AlreadyCorrelatedExceptionFilter().filter(record) is False
    record.exc_info = (RuntimeError, RuntimeError("not previously logged"), None)
    assert app_logging.AlreadyCorrelatedExceptionFilter().filter(record) is True


@pytest.mark.parametrize(
    "route,internal,expected",
    [
        ("/v1/chat/session/interact", False, "chat"),
        ("/v1/chat/admin/sessions", False, "admin"),
        ("/v1/school/{school_id}", False, "admin"),
        ("/v1/process-stripe-event", True, "webhook"),
        ("/v1/process-outbox-events", True, "background"),
        ("/v1/version", True, "health"),
        ("/v1/chat/telemetry", False, "telemetry"),
        ("<unmatched>", False, "other"),
    ],
)
def test_request_classes_are_bounded(route, internal, expected):
    from app.middleware.request_logging import traffic_class

    assert traffic_class(route, internal=internal) == expected


@pytest.mark.asyncio
async def test_request_summary_uses_route_template_not_secrets(monkeypatch):
    from app.middleware.request_logging import RequestLoggingMiddleware

    messages = []
    log = []
    monkeypatch.setattr(
        "app.middleware.request_logging.logger.info",
        lambda event, **fields: log.append(fields),
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
