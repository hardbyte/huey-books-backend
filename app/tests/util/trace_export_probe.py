"""Isolated real-Postgres probe for instrumented pooled checkout overhead."""

import asyncio
import time

from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.logging import create_span_processor


class SlowExporter(SpanExporter):
    def __init__(self):
        self.spans = 0

    def export(self, spans):
        time.sleep(0.05)
        self.spans += len(spans)
        return SpanExportResult.SUCCESS


async def main():
    exporter = SlowExporter()
    provider = TracerProvider()
    provider.add_span_processor(create_span_processor(exporter))
    AsyncPGInstrumentor().instrument(tracer_provider=provider)
    engine = create_async_engine(
        get_settings().SQLALCHEMY_ASYNC_URI, pool_pre_ping=True
    )
    SQLAlchemyInstrumentor().instrument(
        engine=engine.sync_engine, tracer_provider=provider
    )
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        provider.force_flush()
        exporter.spans = 0
        started = time.perf_counter()
        for _ in range(10):
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
                await connection.commit()
        elapsed = time.perf_counter() - started
        provider.force_flush()
        print(f"10 transactions: {elapsed:.3f}s; {exporter.spans} exported spans")
        assert exporter.spans == 70
        assert elapsed < 0.8, "Trace export is blocking instrumented pooled queries"
    finally:
        await engine.dispose()
        provider.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
