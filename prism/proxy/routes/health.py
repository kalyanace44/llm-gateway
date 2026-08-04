"""Health and readiness endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(request: Request):
    """Liveness probe."""
    return {"status": "ok"}


@router.get("/ready")
async def readiness(request: Request):
    """Readiness probe — checks at least one provider is healthy."""
    breakers = request.app.state.breakers
    healthy = breakers.get_healthy()
    if not healthy:
        return {"status": "not_ready", "healthy_providers": 0}
    return {"status": "ready", "healthy_providers": len(healthy)}


@router.get("/health/providers")
async def provider_health(request: Request):
    """Circuit breaker status for all providers."""
    breakers = request.app.state.breakers
    return {"providers": breakers.get_all_status()}


@router.get("/metrics")
async def metrics(request: Request):
    """Prometheus-compatible metrics endpoint."""
    collector = request.app.state.metrics
    return PlainTextResponse(
        collector.prometheus_text(),
        media_type="text/plain; version=0.0.4",
    )
