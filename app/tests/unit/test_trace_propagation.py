import pytest
from opentelemetry import baggage, trace
from opentelemetry.context import Context
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags

from app.observability.propagation import TraceContextPropagator

W3C_TRACE = "12345678901234567890123456789012"
GOOGLE_TRACE = "abcdefabcdefabcdefabcdefabcdefab"
W3C_HEADER = f"00-{W3C_TRACE}-1234567890123456-01"
GOOGLE_HEADER = f"{GOOGLE_TRACE}/123;o=1"


@pytest.mark.parametrize(
    "headers, expected_trace",
    [
        ({"traceparent": W3C_HEADER}, W3C_TRACE),
        ({"x-cloud-trace-context": GOOGLE_HEADER}, GOOGLE_TRACE),
        (
            {"traceparent": W3C_HEADER, "x-cloud-trace-context": GOOGLE_HEADER},
            W3C_TRACE,
        ),
        (
            {"traceparent": "invalid", "x-cloud-trace-context": GOOGLE_HEADER},
            GOOGLE_TRACE,
        ),
    ],
)
def test_incoming_parent_selection(headers, expected_trace):
    context = TraceContextPropagator().extract(headers)
    span = trace.get_current_span(context).get_span_context()
    assert span.is_remote
    assert span.trace_id == int(expected_trace, 16)
    assert span.trace_flags.sampled


def test_ambient_span_does_not_prevent_google_fallback():
    ambient = trace.set_span_in_context(
        NonRecordingSpan(SpanContext(1, 2, False)), Context()
    )
    context = TraceContextPropagator().extract(
        {"traceparent": "invalid", "x-cloud-trace-context": GOOGLE_HEADER},
        context=ambient,
    )
    assert trace.get_current_span(context).get_span_context().trace_id == int(
        GOOGLE_TRACE, 16
    )


def test_invalid_headers_do_not_create_parent():
    context = TraceContextPropagator().extract(
        {"traceparent": "invalid", "x-cloud-trace-context": "invalid"},
        context=Context(),
    )
    assert not trace.get_current_span(context).get_span_context().is_valid


def test_only_w3c_trace_context_is_injected_and_baggage_is_not_extracted():
    propagator = TraceContextPropagator()
    assert propagator.fields == {"traceparent", "tracestate"}
    context = propagator.extract(
        {
            "traceparent": W3C_HEADER,
            "tracestate": "vendor=value",
            "baggage": "child=secret",
        }
    )
    assert not baggage.get_all(context)
    carrier = {}
    propagator.inject(carrier, baggage.set_baggage("child", "secret", context))
    assert carrier == {"traceparent": W3C_HEADER, "tracestate": "vendor=value"}


def test_unsampled_parent_stays_unsampled():
    propagator = TraceContextPropagator()
    context = propagator.extract({"traceparent": W3C_HEADER[:-2] + "00"})
    assert trace.get_current_span(context).get_span_context().trace_flags == TraceFlags(
        0
    )
