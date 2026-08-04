"""POST /v1/chat/completions — the core proxy route."""
from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

router = APIRouter(tags=["proxy"])


class Message(BaseModel):
    role: str
    content: str | None = None
    name: str | None = None
    tool_calls: list | None = None
    tool_call_id: str | None = None


class ChatRequest(BaseModel):
    model: str
    messages: list[Message]
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    stream: bool = False
    stop: str | list[str] | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    user: str | None = None
    # Prism extensions
    prism_cache: bool = True
    prism_metadata: dict | None = None


@router.post("/v1/chat/completions")
async def chat_completions(body: ChatRequest, request: Request):
    """OpenAI-compatible chat completions with routing, caching, resilience."""
    request_id = str(uuid.uuid4())
    start = time.perf_counter()

    # 1. Auth + rate limiting
    keys: "KeyManager" = request.app.state.keys
    api_key = _extract_key(request)
    key_info = keys.validate(api_key)
    if not key_info:
        raise HTTPException(status_code=401, detail="Invalid API key")

    if not keys.check_rate_limit(key_info):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    # 2. Security scan (PII + prompt injection)
    scanner = getattr(request.app.state, "scanner", None)
    if scanner:
        messages_raw = [m.model_dump() for m in body.messages]
        scan_result = scanner.scan_messages(messages_raw)
        if scan_result.action.value == "block":
            raise HTTPException(
                status_code=451,
                detail={
                    "error": "Request blocked by security scanner",
                    "findings": scan_result.findings,
                    "request_id": request_id,
                },
            )
        elif scan_result.action.value == "redact":
            # Replace message content with redacted versions
            for msg in body.messages:
                if msg.content:
                    redacted = scanner.scan(msg.content)
                    if redacted.redacted_content:
                        msg.content = redacted.redacted_content

    # 3. Cache check
    cache: "CacheStore" = request.app.state.cache
    if body.prism_cache and not body.stream:
        cached = cache.get(body.model, body.messages)
        if cached:
            metrics: "MetricsCollector" = request.app.state.metrics
            metrics.record_cache_hit(body.model, key_info.team)
            cached["_prism"] = {"request_id": request_id, "cached": True}
            return cached

    # 4. Route to provider with resilience
    router_svc: "Router" = request.app.state.router
    breakers: "CircuitBreakerRegistry" = request.app.state.breakers
    metrics: "MetricsCollector" = request.app.state.metrics

    payload = body.model_dump(exclude={"prism_cache", "prism_metadata"}, exclude_none=True)

    # Try providers in priority order with circuit breaker gating
    providers = request.app.state.config.get_providers_for_model(body.model)
    last_error = None

    for provider in providers:
        if not breakers.can_execute(provider.name):
            continue

        try:
            if body.stream:
                stream_gen = await router_svc.stream(provider, payload)

                async def _wrap():
                    async for chunk in stream_gen:
                        yield chunk

                latency = time.perf_counter() - start
                breakers.record_success(provider.name)
                metrics.record_request(
                    model=body.model, provider=provider.name,
                    team=key_info.team, latency=latency, stream=True,
                )
                return StreamingResponse(
                    _wrap(),
                    media_type="text/event-stream",
                    headers={
                        "X-Prism-Request-Id": request_id,
                        "X-Prism-Provider": provider.name,
                    },
                )
            else:
                response = await router_svc.complete(provider, payload)
                latency = time.perf_counter() - start

                # Record success
                breakers.record_success(provider.name)
                usage = response.get("usage", {})
                metrics.record_request(
                    model=body.model, provider=provider.name,
                    team=key_info.team, latency=latency,
                    input_tokens=usage.get("prompt_tokens", 0),
                    output_tokens=usage.get("completion_tokens", 0),
                )

                # Cache response
                if body.prism_cache:
                    cache.put(body.model, body.messages, response)

                # Add Prism metadata
                response["_prism"] = {
                    "request_id": request_id,
                    "provider": provider.name,
                    "latency_ms": round(latency * 1000, 1),
                    "cached": False,
                }
                return response

        except Exception as e:
            latency = time.perf_counter() - start
            breakers.record_failure(provider.name)
            metrics.record_error(body.model, provider.name, key_info.team, str(e))
            last_error = e
            continue

    # All providers failed
    raise HTTPException(
        status_code=502,
        detail=f"All providers failed for model {body.model}: {last_error}",
    )


def _extract_key(request: Request) -> str:
    """Extract API key from Authorization header."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return ""
