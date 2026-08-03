"""Admin API — key management, config, and usage dashboards."""
from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/admin")


def _check_admin(request: Request):
    """Verify admin key."""
    from gateway.config import settings

    auth = request.headers.get("authorization", "")
    key = auth.replace("Bearer ", "") if auth.startswith("Bearer ") else ""
    if key != settings.admin_key:
        raise HTTPException(status_code=403, detail="Admin access required")


class CreateKeyRequest(BaseModel):
    name: str
    key: str  # Raw key to register
    rate_limit_rpm: Optional[int] = None
    rate_limit_tpm: Optional[int] = None
    budget_usd: Optional[float] = None
    allowed_models: list[str] = []


@router.post("/keys")
async def create_key(body: CreateKeyRequest, request: Request):
    """Register a new API key."""
    _check_admin(request)
    auth_mgr = request.app.state.auth_manager
    api_key = auth_mgr.register_key(
        raw_key=body.key,
        name=body.name,
        rate_limit_rpm=body.rate_limit_rpm,
        rate_limit_tpm=body.rate_limit_tpm,
        budget_usd=body.budget_usd,
        allowed_models=body.allowed_models,
    )
    return {"status": "created", "name": api_key.name, "key_hash": api_key.key_hash}


@router.get("/keys")
async def list_keys(request: Request):
    """List all registered API keys."""
    _check_admin(request)
    auth_mgr = request.app.state.auth_manager
    keys = auth_mgr.list_keys()
    return {
        "keys": [
            {
                "name": k.name,
                "enabled": k.enabled,
                "budget_usd": k.budget_usd,
                "spent_usd": round(k.spent_usd, 4),
                "allowed_models": k.allowed_models,
            }
            for k in keys
        ]
    }


@router.delete("/keys/{key}")
async def revoke_key(key: str, request: Request):
    """Revoke an API key."""
    _check_admin(request)
    auth_mgr = request.app.state.auth_manager
    if auth_mgr.revoke_key(key):
        return {"status": "revoked"}
    raise HTTPException(status_code=404, detail="Key not found")


@router.get("/usage")
async def get_usage(request: Request, hours: int = 24):
    """Get usage summary for the last N hours."""
    _check_admin(request)
    tracker = request.app.state.tracker
    since = time.time() - (hours * 3600)
    return tracker.get_summary(since)


@router.get("/backends")
async def list_backends(request: Request):
    """List all configured backends and their health."""
    _check_admin(request)
    router_svc = request.app.state.router
    return {
        "backends": [
            {
                "name": b.name,
                "provider": b.provider,
                "base_url": b.base_url,
                "models": b.models,
                "enabled": b.enabled,
                "healthy": b.healthy,
                "weight": b.weight,
                "priority": b.priority,
            }
            for b in router_svc.backends
        ]
    }


@router.post("/backends/{name}/health")
async def check_backend_health(name: str, request: Request):
    """Trigger health check for a specific backend."""
    _check_admin(request)
    router_svc = request.app.state.router
    for b in router_svc.backends:
        if b.name == name:
            healthy = await b.health_check()
            return {"name": name, "healthy": healthy}
    raise HTTPException(status_code=404, detail=f"Backend '{name}' not found")
