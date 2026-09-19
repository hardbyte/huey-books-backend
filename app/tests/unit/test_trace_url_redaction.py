import json
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.trace import Event, ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Link, SpanContext, Status, StatusCode

from app.observability.privacy import redact
from app.observability.tracing import RedactingSpanProcessor, RequestSampler


@pytest.mark.parametrize(
    "attribute", ["http.target", "url.path", "http.url", "url.full"]
)
@pytest.mark.parametrize("prefix", ["", "https://example.com"])
def test_url_attributes_preserve_path_and_remove_query(attribute, prefix):
    value = f"{prefix}/v1/search?query=private-search&reader_id=private-reader"
    assert redact(value, attribute) == f"{prefix}/v1/search?[redacted]"


@pytest.mark.parametrize(
    "attribute", ["url.query", "query", "query_string", "http.query_string"]
)
def test_query_attributes_are_redacted_entirely(attribute):
    assert (
        redact("query=private-search&reader_id=private-reader", attribute)
        == "[redacted]"
    )


@pytest.mark.timeout(2)
@pytest.mark.parametrize(
    "suffix,expected_suffix", [("", ""), (" search?query=private", " search?[redacted]")]
)
def test_queryless_slash_rich_paths_redact_without_repeated_suffix_scans(
    suffix, expected_suffix
):
    path = "/" * 40000 + "path"
    assert redact(path + suffix) == path + expected_suffix


def test_url_embedded_in_diagnostic_preserves_prefix_and_sql_attributes():
    assert redact("url=/v1/search?query=private-search") == "url=/v1/search?[redacted]"
    statement = "SELECT data FROM works WHERE id = $1"
    assert redact(statement, "db.query.text") == statement


@pytest.mark.parametrize("path", ["/v1/search", "search", ""])
def test_relative_url_queries_are_removed_from_all_exported_span_fields(path):
    private_url = f"{path}?query=private-search&reader_id=private-reader"
    processor = MagicMock()
    RedactingSpanProcessor(processor).on_end(
        ReadableSpan(
            name=f"GET {private_url}",
            attributes={"http.target": private_url, "url.query": "private-search"},
            events=[Event(f"request {private_url}", {"url.full": private_url})],
            links=[
                Link(
                    SpanContext(trace_id=42, span_id=7, is_remote=True),
                    {"url.query": "private-reader"},
                )
            ],
            status=Status(StatusCode.ERROR, f"Request failed: {private_url}"),
        )
    )
    exported = processor.on_end.call_args.args[0]
    assert "private-search" not in exported.to_json()
    assert "private-reader" not in exported.to_json()
    assert exported.attributes["http.target"] == f"{path}?[redacted]"


def test_question_punctuation_and_sql_operators_remain_usable():
    assert redact("Request failed? Retry.") == "Request failed? Retry."
    statement = "SELECT data FROM works WHERE data ? 'key' AND id = $1"
    assert redact(statement, "db.query.text") == statement


def test_sampled_read_request_redacts_query_before_export():
    exporter = InMemorySpanExporter()
    provider = TracerProvider(sampler=RequestSampler(chat_rate=0, read_rate=1))
    provider.add_span_processor(RedactingSpanProcessor(SimpleSpanProcessor(exporter)))
    app = FastAPI()

    @app.get("/v1/search")
    def search():
        return {"ok": True}

    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
    try:
        with TestClient(app) as client:
            response = client.get(
                "/v1/search?query=private-search&reader_id=private-reader",
                headers={
                    "traceparent": "00-0000000000000000000000000000002a-0000000000000007-00"
                },
            )
        assert response.status_code == 200
        spans = exporter.get_finished_spans()
        assert spans
        serialized = json.dumps([json.loads(span.to_json()) for span in spans])
        assert "private-search" not in serialized
        assert "private-reader" not in serialized
        assert "/v1/search" in serialized
    finally:
        FastAPIInstrumentor.uninstrument_app(app)
        provider.shutdown()
