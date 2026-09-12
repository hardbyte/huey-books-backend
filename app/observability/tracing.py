from collections.abc import Sequence

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.sdk.trace import Event, ReadableSpan, Span, SpanProcessor
from opentelemetry.sdk.trace.sampling import (
    ALWAYS_OFF,
    ALWAYS_ON,
    ParentBased,
    Sampler,
    SamplingResult,
    TraceIdRatioBased,
)
from opentelemetry.trace import Link, SpanKind, Status, TraceState
from opentelemetry.util.types import Attributes

from app.observability.privacy import is_database_exception_type, redact


class RequestSampler(Sampler):
    def __init__(self, chat_rate: float):
        self.chat = TraceIdRatioBased(chat_rate)
        self.other = ParentBased(ALWAYS_ON)

    def should_sample(
        self,
        parent_context: Context | None,
        trace_id: int,
        name: str,
        kind: SpanKind | None = None,
        attributes: Attributes = None,
        links: Sequence[Link] | None = None,
        trace_state: TraceState | None = None,
    ) -> SamplingResult:
        sampler = self.other
        if kind == trace.SpanKind.SERVER:
            attributes = attributes or {}
            path = attributes.get("url.path", attributes.get("http.target", "")).split(
                "?", 1
            )[0]
            if path in ("/v1/version", "/v1/chat/telemetry"):
                sampler = ALWAYS_OFF
            elif path.startswith("/v1/chat/") and not path.startswith(
                "/v1/chat/admin/"
            ):
                # Cloud Run often supplies an unsampled remote parent. Sample the
                # complete chat subtree, including slow responses, at a bounded rate.
                sampler = self.chat
        return sampler.should_sample(
            parent_context, trace_id, name, kind, attributes, links, trace_state
        )

    def get_description(self) -> str:
        return f"RequestSampler(chat={self.chat.get_description()})"


class RedactingSpanProcessor(SpanProcessor):
    def __init__(self, processor: SpanProcessor):
        self.processor = processor

    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        self.processor.on_start(span, parent_context)

    def on_end(self, span: ReadableSpan) -> None:
        is_database = bool(
            (span.attributes or {}).get("db.system")
            or (span.attributes or {}).get("db.statement")
            or (span.attributes or {}).get("db.system.name")
            or (span.attributes or {}).get("db.query.text")
        )
        has_database_error = any(
            is_database_exception_type(
                (event.attributes or {}).get("exception.type", "")
            )
            for event in span.events
        )
        events = []
        for event in span.events:
            attributes = redact(event.attributes or {})
            if (is_database or has_database_error) and event.name == "exception":
                # Database errors can echo bound values even when SQLAlchemy
                # hides the parameter collection in its exception representation.
                attributes = {
                    key: value
                    for key, value in attributes.items()
                    if key not in {"exception.message", "exception.stacktrace"}
                }
            events.append(Event(redact(event.name), attributes, event.timestamp))
        self.processor.on_end(
            ReadableSpan(
                name=redact(span.name),
                context=span.context,
                parent=span.parent,
                resource=span.resource,
                attributes=redact(span.attributes),
                events=events,
                links=[
                    Link(link.context, redact(link.attributes)) for link in span.links
                ],
                kind=span.kind,
                status=Status(
                    span.status.status_code,
                    None
                    if is_database or has_database_error
                    else redact(span.status.description),
                ),
                start_time=span.start_time,
                end_time=span.end_time,
                instrumentation_scope=span.instrumentation_scope,
            )
        )

    def shutdown(self) -> None:
        self.processor.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self.processor.force_flush(timeout_millis)
