"""Self-healing health API — circuit breaker status, drift detection, recovery actions."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/v1/health", tags=["health"])


def _check_admin(request: Request):
    from gateway.config import settings
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing auth")
    token = auth[7:]
    if token != settings.admin_key:
        raise HTTPException(status_code=403, detail="Invalid admin key")


@router.get("/backends")
async def backend_health(request: Request):
    """Get health status for all backends with circuit breaker state."""
    healer = request.app.state.healer
    return {
        "backends": healer.get_all_status(),
        "healthy_count": len(healer.get_healthy_backends()),
        "total_count": len(healer._backends),
    }


@router.get("/backends/{name}")
async def backend_health_detail(name: str, request: Request):
    """Get detailed health for a specific backend."""
    healer = request.app.state.healer
    status = healer.get_backend_status(name)
    if not status:
        raise HTTPException(status_code=404, detail=f"Backend '{name}' not found")
    return status


@router.post("/backends/{name}/reset")
async def reset_backend(name: str, request: Request):
    """Force reset a backend's circuit breaker."""
    _check_admin(request)
    healer = request.app.state.healer
    success = healer.reset_backend(name)
    if not success:
        raise HTTPException(status_code=404, detail=f"Backend '{name}' not found")
    return {"status": "reset", "backend": name}


@router.get("/actions")
async def healing_actions(request: Request, limit: int = 20):
    """Get recent self-healing actions log."""
    healer = request.app.state.healer
    return {"actions": healer.get_actions_log(limit=limit)}


@router.get("/summary")
async def health_summary(request: Request):
    """Overall platform health summary."""
    healer = request.app.state.healer
    all_status = healer.get_all_status()

    healthy = [s for s in all_status if s["healthy"]]
    degraded = [s for s in all_status if not s["healthy"] and s["health_score"] > 0]
    down = [s for s in all_status if s["health_score"] == 0]

    # Determine overall status
    if not all_status:
        overall = "unknown"
    elif len(healthy) == len(all_status):
        overall = "healthy"
    elif len(down) == len(all_status):
        overall = "down"
    elif down:
        overall = "degraded"
    else:
        overall = "degraded"

    return {
        "status": overall,
        "healthy": len(healthy),
        "degraded": len(degraded),
        "down": len(down),
        "total": len(all_status),
        "backends": {
            s["backend"]: {
                "state": s["circuit_breaker"]["state"] if s["circuit_breaker"] else "unknown",
                "health_score": s["health_score"],
                "error_rate": s["error_rate_5m"],
            }
            for s in all_status
        },
    }
