"""Prometheus metrics for the LLM Gateway."""
from __future__ import annotations

import time

from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    Info,
    generate_latest,
    CONTENT_TYPE_LATEST,
)
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


# --- Counters ---
REQUEST_COUNT = Counter(
    "llm_gateway_requests_total",
    "Total requests to the gateway",
    ["method", "endpoint", "status_code"],
)

TOKEN_COUNT = Counter(
    "llm_gateway_tokens_total",
    "Total tokens processed",
    ["model", "backend", "direction"],  # direction: input | output
)

COST_TOTAL = Counter(
    "llm_gateway_cost_usd_total",
    "Total cost in USD",
    ["model", "backend", "api_key"],
)

BACKEND_ERRORS = Counter(
    "llm_gateway_backend_errors_total",
    "Backend errors by type",
    ["backend", "error_type"],  # error_type: timeout | connection | 5xx
)

# --- Histograms ---
REQUEST_LATENCY = Histogram(
    "llm_gateway_request_duration_seconds",
    "Request latency in seconds",
    ["model", "backend"],
    buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0],
)

TTFT_LATENCY = Histogram(
    "llm_gateway_ttft_seconds",
    "Time to first token (streaming)",
    ["model", "backend"],
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

TOKENS_PER_SECOND = Histogram(
    "llm_gateway_tokens_per_second",
    "Output tokens per second throughput",
    ["model", "backend"],
    buckets=[1, 5, 10, 20, 50, 100, 200],
)

# --- Gauges ---
ACTIVE_REQUESTS = Gauge(
    "llm_gateway_active_requests",
    "Currently processing requests",
    ["model"],
)

BACKEND_HEALTHY = Gauge(
    "llm_gateway_backend_healthy",
    "Backend health status (1=healthy, 0=unhealthy)",
    ["backend"],
)

# --- Info ---
GATEWAY_INFO = Info(
    "llm_gateway",
    "Gateway build info",
)
GATEWAY_INFO.info({"version": "0.1.0", "name": "llm-gateway"})


def record_request(
    model: str,
    backend: str,
    api_key_hash: str,
    input_tokens: int,
    output_tokens: int,
    latency_seconds: float,
    cost_usd: float,
    ttft_seconds: float = 0.0,
):
    """Record metrics for a completed request."""
    TOKEN_COUNT.labels(model=model, backend=backend, direction="input").inc(input_tokens)
    TOKEN_COUNT.labels(model=model, backend=backend, direction="output").inc(output_tokens)
    COST_TOTAL.labels(model=model, backend=backend, api_key=api_key_hash[:8]).inc(cost_usd)
    REQUEST_LATENCY.labels(model=model, backend=backend).observe(latency_seconds)

    if ttft_seconds > 0:
        TTFT_LATENCY.labels(model=model, backend=backend).observe(ttft_seconds)

    if output_tokens > 0 and latency_seconds > 0:
        tps = output_tokens / latency_seconds
        TOKENS_PER_SECOND.labels(model=model, backend=backend).observe(tps)


def record_backend_error(backend: str, error_type: str):
    """Record a backend error."""
    BACKEND_ERRORS.labels(backend=backend, error_type=error_type).inc()


def update_backend_health(backend: str, healthy: bool):
    """Update backend health gauge."""
    BACKEND_HEALTHY.labels(backend=backend).set(1 if healthy else 0)


class MetricsMiddleware(BaseHTTPMiddleware):
    """Middleware to track HTTP request metrics."""

    async def dispatch(self, request: Request, call_next):
        start = time.time()
        response = await call_next(request)
        duration = time.time() - start

        # Skip metrics endpoint itself
        if request.url.path != "/metrics":
            REQUEST_COUNT.labels(
                method=request.method,
                endpoint=request.url.path,
                status_code=response.status_code,
            ).inc()

        return response


async def metrics_endpoint(request: Request) -> Response:
    """Prometheus metrics scrape endpoint."""
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )
