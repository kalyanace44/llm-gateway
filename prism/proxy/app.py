"""Prism proxy — FastAPI application factory."""
from __future__ import annotations

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from prism.config import PrismConfig


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle — initialize subsystems."""
    from prism.auth.keys import KeyManager
    from prism.cache.store import CacheStore
    from prism.observe.metrics import MetricsCollector
    from prism.resilience.circuit_breaker import CircuitBreakerRegistry
    from prism.routing.router import Router
    from prism_cloud.scanner import SecurityScanner

    cfg: PrismConfig = app.state.config

    app.state.router = Router(cfg)
    app.state.breakers = CircuitBreakerRegistry(cfg.resilience)
    app.state.cache = CacheStore(cfg.cache)
    app.state.keys = KeyManager(cfg)
    app.state.metrics = MetricsCollector()
    app.state.scanner = SecurityScanner()

    # Register providers with circuit breakers
    for provider in cfg.providers:
        app.state.breakers.register(provider.name)

    yield

    # Cleanup
    await app.state.router.close()


class TimingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        response: Response = await call_next(request)
        elapsed = time.perf_counter() - start
        response.headers["X-Request-Time-Ms"] = f"{elapsed * 1000:.1f}"
        return response


def create_app(config: PrismConfig | None = None) -> FastAPI:
    """Create the Prism gateway application."""
    if config is None:
        config = PrismConfig.from_env()

    app = FastAPI(
        title="Prism",
        description="AI Traffic Control Plane",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
    )
    app.state.config = config

    # Middleware
    app.add_middleware(TimingMiddleware)

    # Routes
    from prism.dashboard import router as dashboard_router
    from prism.proxy.routes import admin, chat, health, models
    app.include_router(chat.router)
    app.include_router(models.router)
    app.include_router(health.router)
    app.include_router(admin.router)
    app.include_router(dashboard_router)

    return app
