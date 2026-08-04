"""GET /v1/models — list available models across all providers."""
from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(tags=["proxy"])


@router.get("/v1/models")
async def list_models(request: Request):
    """List all models available across configured providers."""
    config = request.app.state.config
    breakers = request.app.state.breakers

    models = []
    seen = set()
    for provider in config.providers:
        for model in provider.models:
            if model not in seen:
                seen.add(model)
                healthy = breakers.can_execute(provider.name)
                models.append({
                    "id": model,
                    "object": "model",
                    "owned_by": provider.name,
                    "available": healthy,
                })

    return {"object": "list", "data": models}
