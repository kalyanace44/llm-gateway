"""LLM Gateway — main application entry point."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from gateway.config import config, settings
from gateway.core.auth import AuthManager
from gateway.core.backends import Backend
from gateway.core.costs import UsageTracker
from gateway.core.limiter import RateLimiter
from gateway.core.router import Router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize and tear down application state."""
    # Build backends from config
    backends = [Backend(cfg) for cfg in config.backends if cfg.enabled]

    # Initialize services
    app.state.auth_manager = AuthManager()
    app.state.limiter = RateLimiter(
        default_rpm=config.rate_limits.requests_per_minute,
        default_tpm=config.rate_limits.tokens_per_minute,
    )
    app.state.tracker = UsageTracker()
    app.state.router = Router(backends, config)

    # Register a default key for dev
    if settings.debug:
        app.state.auth_manager.register_key("dev-key", "development")

    yield

    # Shutdown: close all backend connections
    for b in backends:
        await b.close()


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

app.include_router(chat_router)
app.include_router(models_router)
app.include_router(admin_router)


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
