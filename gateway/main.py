"""LLM Gateway — main application entry point."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from gateway.config import config, settings
from gateway.core.auth import AuthManager
from gateway.core.backends import Backend
from gateway.core.cache import ResponseCache
from gateway.core.costs import UsageTracker
from gateway.core.healing import SelfHealingManager
from gateway.core.limiter import RateLimiter
from gateway.core.metrics import MetricsMiddleware, metrics_endpoint, update_backend_health
from gateway.core.router import Router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize and tear down application state."""
    # Initialize database
    from gateway.db.session import init_db, close_db
    await init_db()

    # Build backends from config
    backends = [Backend(cfg) for cfg in config.backends if cfg.enabled]

    # Initialize services
    app.state.auth_manager = AuthManager()
    app.state.limiter = RateLimiter(
        default_rpm=config.rate_limits.requests_per_minute,
        default_tpm=config.rate_limits.tokens_per_minute,
    )
    app.state.tracker = UsageTracker()
    app.state.cache = ResponseCache(
        ttl=config.cache_ttl,
        max_size=10_000,
    )
    app.state.healer = SelfHealingManager()
    app.state.router = Router(backends, config)

    # Register backends with self-healing
    for b in backends:
        app.state.healer.register_backend(b.name)

    # Register a default key for dev
    if settings.debug:
        app.state.auth_manager.register_key("dev-key", "development")

    # Set initial backend health metrics
    for b in backends:
        update_backend_health(b.name, b.healthy)

    yield

    # Shutdown: close all backend connections
    for b in backends:
        await b.close()
    await close_db()


app = FastAPI(
    title="LLM Gateway",
    description="Production LLM Gateway — multi-model routing, cost tracking, observability",
    version="0.1.0",
    lifespan=lifespan,
)

# Register routers
from gateway.api.chat import router as chat_router
from gateway.api.models import router as models_router
from gateway.api.admin import router as admin_router
from gateway.api.tenants import router as tenants_router
from gateway.api.memory import router as memory_router
from gateway.api.rag import router as rag_router
from gateway.api.health import router as health_router
from gateway.api.deploy import router as deploy_router

app.include_router(chat_router)
app.include_router(models_router)
app.include_router(admin_router)
app.include_router(tenants_router)
app.include_router(memory_router)
app.include_router(rag_router)
app.include_router(health_router)
app.include_router(deploy_router)
app.add_middleware(MetricsMiddleware)
app.add_route("/metrics", metrics_endpoint)


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok"}


def main():
    """Run with uvicorn."""
    import uvicorn

    uvicorn.run(
        "gateway.main:app",
        host=settings.host,
        port=settings.port,
        workers=settings.workers,
        reload=settings.debug,
    )


if __name__ == "__main__":
    main()
