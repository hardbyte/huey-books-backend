"""Isolated probe for tracing every lazily created database engine."""

import argparse
import asyncio
from pathlib import Path

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.config import get_settings
from app.db.session import _get_async_session_maker, _get_session_maker
from app.logging import init_tracing
from app.observability.privacy import request_secrets
from app.observability.tracing import RedactingSpanProcessor


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlite-directory", type=Path)
    args = parser.parse_args()
    settings = get_settings().model_copy(update={"ENABLE_OTEL_GOOGLE_EXPORTER": False})
    init_tracing(FastAPI(), settings)
    provider = trace.get_tracer_provider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(RedactingSpanProcessor(SimpleSpanProcessor(exporter)))
    secret = "synthetic-bound-value-not-a-request-credential"
    context = request_secrets.set(("different-synthetic-session-credential",))
    statement = text("SELECT :bound_value")
    engines_tested = 0
    statements = []
    parameter_hiding = []

    def verify_query_span() -> None:
        spans = exporter.get_finished_spans()
        query_spans = [
            span
            for span in spans
            if span.attributes.get("db.statement", "").upper().startswith("SELECT")
        ]
        assert len(query_spans) == 1, (
            f"Expected one query span, saw {[span.name for span in spans]}"
        )
        assert query_spans[0].parent is not None
        assert secret not in "".join(span.to_json() for span in spans)
        statements.append(query_spans[0].attributes["db.statement"])

    def verify_error_privacy() -> None:
        spans = exporter.get_finished_spans()
        database_spans = [span for span in spans if span.attributes.get("db.system")]
        assert any(span.status.is_ok is False for span in database_spans)
        assert secret not in "".join(span.to_json() for span in database_spans)

    try:
        for number in range(2):
            uri = (
                f"sqlite:///{args.sqlite_directory / f'engine-{number}.db'}"
                if args.sqlite_directory
                else settings.SQLALCHEMY_DATABASE_URI.render_as_string(
                    hide_password=False
                )
            )
            factory = _get_session_maker(uri, number + 1, 0)
            engine = factory.kw["bind"]
            try:
                exporter.clear()
                with provider.get_tracer(__name__).start_as_current_span("sync query"):
                    with factory() as session:
                        assert (
                            session.execute(
                                statement, {"bound_value": secret}
                            ).scalar_one()
                            == secret
                        )
                verify_query_span()
                parameter_hiding.append(engine.hide_parameters)
                engines_tested += 1
                exporter.clear()
                with provider.get_tracer(__name__).start_as_current_span("sync error"):
                    try:
                        with factory() as session:
                            invalid_query = (
                                "SELECT missing_column WHERE :bound_value IS NULL"
                                if args.sqlite_directory
                                else "SELECT CAST(:bound_value AS INTEGER)"
                            )
                            session.execute(
                                text(invalid_query), {"bound_value": secret}
                            )
                    except DBAPIError:
                        pass
                    else:
                        raise AssertionError("Invalid query unexpectedly succeeded")
                verify_error_privacy()
            finally:
                engine.dispose()

        if not args.sqlite_directory:

            async def async_queries() -> None:
                nonlocal engines_tested
                for number in range(2):
                    factory = _get_async_session_maker(
                        settings.SQLALCHEMY_ASYNC_URI.render_as_string(
                            hide_password=False
                        ),
                        number + 1,
                        0,
                    )
                    engine = factory.kw["bind"]
                    try:
                        exporter.clear()
                        with provider.get_tracer(__name__).start_as_current_span(
                            "async query"
                        ):
                            async with factory() as session:
                                assert (
                                    await session.execute(
                                        statement, {"bound_value": secret}
                                    )
                                ).scalar_one() == secret
                        verify_query_span()
                        parameter_hiding.append(engine.sync_engine.hide_parameters)
                        engines_tested += 1
                        exporter.clear()
                        with provider.get_tracer(__name__).start_as_current_span(
                            "async error"
                        ):
                            try:
                                async with factory() as session:
                                    await session.execute(
                                        text("SELECT CAST(:bound_value AS INTEGER)"),
                                        {"bound_value": secret},
                                    )
                            except DBAPIError:
                                pass
                            else:
                                raise AssertionError(
                                    "Invalid query unexpectedly succeeded"
                                )
                        verify_error_privacy()
                    finally:
                        await engine.dispose()

            # Session factories are cached per loop; both new loops must be traced.
            asyncio.run(async_queries())
            asyncio.run(async_queries())
        assert all(parameter_hiding)
        assert all("traceparent" not in statement for statement in statements)
        print(f"Verified query spans and parameter privacy on {engines_tested} engines")
    finally:
        request_secrets.reset(context)
        provider.shutdown()


if __name__ == "__main__":
    main()
