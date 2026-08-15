"""Smart routing — multi-provider load balancing with fallback."""
from __future__ import annotations

from collections.abc import AsyncGenerator

import httpx

from prism.config import PrismConfig, ProviderConfig


class Router:
    """Routes requests to LLM providers with connection pooling."""

    def __init__(self, config: PrismConfig):
        self.config = config
        self._clients: dict[str, httpx.AsyncClient] = {}
        for p in config.providers:
            self._clients[p.name] = httpx.AsyncClient(
                base_url=p.base_url.rstrip("/"),
                timeout=httpx.Timeout(p.timeout, connect=10.0),
                headers={"Authorization": f"Bearer {p.api_key}"},
                limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            )

    async def complete(self, provider: ProviderConfig, payload: dict) -> dict:
        """Non-streaming completion."""
        client = self._clients[provider.name]
        resp = await client.post("/v1/chat/completions", json=payload)
        if resp.status_code != 200:
            raise ProviderError(provider.name, resp.status_code, resp.text[:200])
        return resp.json()

    async def stream(self, provider: ProviderConfig, payload: dict) -> AsyncGenerator[bytes, None]:
        """Streaming completion — yields raw SSE chunks."""
        client = self._clients[provider.name]
        payload["stream"] = True
        async with client.stream("POST", "/v1/chat/completions", json=payload) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                raise ProviderError(provider.name, resp.status_code, body.decode()[:200])
            async for chunk in resp.aiter_bytes():
                yield chunk

    async def close(self):
        """Close all HTTP clients."""
        for client in self._clients.values():
            await client.aclose()


class ProviderError(Exception):
    """A provider returned an error."""

    def __init__(self, provider: str, status_code: int, detail: str):
        self.provider = provider
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"{provider} returned {status_code}: {detail}")
