"""Models API — /v1/models endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/v1/models")
async def list_models(request: Request):
    """List all available models across backends."""
    router_svc = request.app.state.router
    models = router_svc.available_models()
    return {
        "object": "list",
        "data": models,
    }
