"""Application factory shared by every ORDO service."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from ordo_runtime.errors import OrdoError
from ordo_runtime.health import parse_tcp_checks, run_readiness
from ordo_runtime.logs import configure_logging

logger = logging.getLogger("ordo.runtime")

REQUEST_TIMEOUT_S = float(os.environ.get("REQUEST_TIMEOUT_S", "30"))

Handler = Callable[[Request], Awaitable[Response]]


def _trace_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def create_app(service: str, version: str = "0.1.0") -> FastAPI:
    configure_logging(service, os.environ.get("LOG_LEVEL", "INFO"))

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logger.info("service started")
        yield
        logger.info("service stopping (graceful shutdown)")

    app = FastAPI(title=f"ordo-{service}", version=version, lifespan=lifespan)

    _setup_otel(app, service)

    @app.middleware("http")
    async def request_context(request: Request, call_next: Handler) -> Response:
        request.state.request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT_S):
                response = await call_next(request)
        except TimeoutError:
            payload = OrdoError(
                "La operación excedió el tiempo máximo.",
                code="REQUEST_TIMEOUT",
                status_code=504,
                retryable=True,
                hint="Reintenta; si persiste, divide la operación en lotes más pequeños.",
            ).to_payload(trace_id=_trace_id(request))
            response = JSONResponse(payload, status_code=504)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(OrdoError)
    async def ordo_error_handler(request: Request, exc: OrdoError) -> JSONResponse:
        payload = exc.to_payload(trace_id=_trace_id(request))
        return JSONResponse(payload, status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error", extra={"request_id": _trace_id(request)})
        payload = OrdoError(
            "Error interno inesperado.",
            code="INTERNAL_ERROR",
            retryable=True,
            hint="Reintenta con el mismo Idempotency-Key; si persiste, reporta el trace_id.",
        ).to_payload(trace_id=_trace_id(request))
        return JSONResponse(payload, status_code=500)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "service": service}

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        checks = parse_tcp_checks()
        results = await run_readiness(checks)
        ready = all(results.values())
        return JSONResponse(
            {"status": "ok" if ready else "degraded", "service": service, "checks": results},
            status_code=200 if ready else 503,
        )

    return app


def _setup_otel(app: FastAPI, service: str) -> None:
    """Instrument with OpenTelemetry when an OTLP endpoint is configured."""
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.name": f"ordo-{service}"}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)
