"""Chat completions API — OpenAI-compatible /v1/chat/completions endpoint."""
from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from gateway.core.auth import APIKey
from gateway.core.backends import BackendError


router = APIRouter()


class ChatMessage(BaseModel):
    role: str
    content: str | list | None = None
    name: Optional[str] = None


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[ChatMessage]
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_tokens: Optional[int] = None
    stream: bool = False
    stop: Optional[str | list[str]] = None
    presence_penalty: Optional[float] = None
    frequency_penalty: Optional[float] = None
    user: Optional[str] = None

    # Gateway extensions
    fallback: bool = Field(default=True, description="Enable fallback chain")
    cache: bool = Field(default=True, description="Allow cached responses")


@router.post("/v1/chat/completions")
async def chat_completions(body: ChatCompletionRequest, request: Request):
    """OpenAI-compatible chat completions with routing, fallback, and cost tracking."""
    app = request.app
    router_svc = app.state.router
    limiter = app.state.limiter
    tracker = app.state.tracker
    auth_mgr = app.state.auth_manager

    # Auth
    auth_header = request.headers.get("authorization", "")
    raw_key = auth_header.replace("Bearer ", "") if auth_header.startswith("Bearer ") else ""
    api_key: Optional[APIKey] = auth_mgr.validate(raw_key)

    if not api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API Key")

    # Model access check
    if not auth_mgr.check_model_access(api_key, body.model):
        raise HTTPException(status_code=403, detail=f"Key does not have access to model: {body.model}")

    # Rate limit (requests)
    allowed, retry_after = limiter.check_request(
        api_key.key_hash, api_key.rate_limit_rpm
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(int(retry_after + 1))},
        )

    # Count input tokens
    input_tokens = tracker.count_messages_tokens(
        [m.model_dump() for m in body.messages], body.model
    )

    # Build payload (strip gateway-specific fields)
    payload = body.model_dump(exclude_none=True, exclude={"fallback", "cache"})

    start = time.time()

    try:
        if body.stream:
            backend, stream_gen = await router_svc.route_with_fallback(
                body.model, payload, stream=True
            )

            async def stream_wrapper():
                async for chunk in stream_gen:
                    yield chunk

            return StreamingResponse(
                stream_wrapper(),
                media_type="text/event-stream",
                headers={"X-Backend": backend.name},
            )
        else:
            backend, response = await router_svc.route_with_fallback(
                body.model, payload, stream=False
            )

            latency_ms = (time.time() - start) * 1000

            # Extract usage from response
            usage = response.get("usage", {})
            output_tokens = usage.get("completion_tokens", 0)
            actual_input = usage.get("prompt_tokens", input_tokens)

            # Record usage
            tracker.record(
                api_key_hash=api_key.key_hash,
                model=body.model,
                backend=backend.name,
                input_tokens=actual_input,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
            )

            # Record cost against key
            cost = tracker.calculate_cost(body.model, actual_input, output_tokens)
            auth_mgr.record_usage(api_key, cost)

            return response

    except BackendError as e:
        latency_ms = (time.time() - start) * 1000
        tracker.record(
            api_key_hash=api_key.key_hash,
            model=body.model,
            backend="none",
            input_tokens=input_tokens,
            output_tokens=0,
            latency_ms=latency_ms,
            status="error",
        )
        raise HTTPException(status_code=e.status_code, detail=e.message)
