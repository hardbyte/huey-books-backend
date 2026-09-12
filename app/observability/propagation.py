from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.propagators.cloud_trace_propagator import CloudTraceFormatPropagator
from opentelemetry.propagators.textmap import (
    CarrierT,
    Getter,
    Setter,
    TextMapPropagator,
    default_getter,
    default_setter,
)
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator


class TraceContextPropagator(TextMapPropagator):
    """Prefer W3C context, accept Cloud Run context, and never propagate baggage."""

    def __init__(self) -> None:
        self._w3c = TraceContextTextMapPropagator()
        self._google = CloudTraceFormatPropagator()

    def extract(
        self,
        carrier: CarrierT,
        context: Context | None = None,
        getter: Getter = default_getter,
    ) -> Context:
        # An ambient span must not make an invalid incoming header look valid.
        incoming = self._w3c.extract(carrier, context=Context(), getter=getter)
        if trace.get_current_span(incoming).get_span_context().is_valid:
            return self._w3c.extract(carrier, context=context, getter=getter)
        return self._google.extract(carrier, context=context, getter=getter)

    def inject(
        self,
        carrier: CarrierT,
        context: Context | None = None,
        setter: Setter = default_setter,
    ) -> None:
        self._w3c.inject(carrier, context=context, setter=setter)

    @property
    def fields(self) -> set[str]:
        return self._w3c.fields
