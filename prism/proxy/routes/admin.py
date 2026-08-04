"""Admin API — key management, cache control, provider management."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/admin", tags=["admin"])


def _check_admin(request: Request):
    """Validate admin key."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing auth")
    keys = request.app.state.keys
    key_info = keys.validate(auth[7:])
    if not key_info or key_info.team != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")


class RegisterKeyRequest(BaseModel):
    key: str
    name: str
    team: str = "default"
    requests_per_minute: int = 600
    budget_usd_per_day: float = 0.0


class RevokeKeyRequest(BaseModel):
    key: str


@router.post("/keys")
async def register_key(body: RegisterKeyRequest, request: Request):
    """Register a new API key."""
    _check_admin(request)
    from prism.config import RateLimitConfig
    keys = request.app.state.keys
    info = keys.register_key(
        raw_key=body.key,
        name=body.name,
        team=body.team,
        rate_limit=RateLimitConfig(requests_per_minute=body.requests_per_minute),
        budget_usd_per_day=body.budget_usd_per_day,
    )
    return {"status": "registered", "name": info.name, "team": info.team}


@router.delete("/keys")
async def revoke_key(body: RevokeKeyRequest, request: Request):
    """Revoke an API key."""
    _check_admin(request)
    keys = request.app.state.keys
    success = keys.revoke_key(body.key)
    if not success:
        raise HTTPException(status_code=404, detail="Key not found")
    return {"status": "revoked"}


@router.get("/keys")
async def list_keys(request: Request):
    """List all registered keys."""
    _check_admin(request)
    keys = request.app.state.keys
    return {"keys": keys.list_keys()}


@router.get("/stats")
async def stats(request: Request):
    """Full platform statistics."""
    _check_admin(request)
    metrics = request.app.state.metrics
    cache = request.app.state.cache
    breakers = request.app.state.breakers
    return {
        "metrics": metrics.summary,
        "cache": cache.stats,
        "providers": breakers.get_all_status(),
    }


@router.post("/cache/invalidate")
async def invalidate_cache(request: Request, model: str | None = None):
    """Invalidate response cache."""
    _check_admin(request)
    cache = request.app.state.cache
    cache.invalidate(model)
    return {"status": "invalidated", "model": model or "all"}


@router.post("/providers/{name}/reset")
async def reset_provider(name: str, request: Request):
    """Reset a provider's circuit breaker."""
    _check_admin(request)
    breakers = request.app.state.breakers
    breakers.reset(name)
    return {"status": "reset", "provider": name}
