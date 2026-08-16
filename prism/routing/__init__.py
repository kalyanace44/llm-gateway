"""Smart routing — multi-provider load balancing with fallback."""
from __future__ import annotations

from collections.abc import AsyncGenerator

import httpx

from prism.config import PrismConfig, ProviderConfig
from prism.routing.adapters import ProviderAdapter, get_adapter


class Router:
    """Routes requests to LLM providers with connection pooling and format translation."""

    def __init__(self, config: PrismConfig):
        self.config = config
        self._clients: dict[str, httpx.AsyncClient] = {}
        self._adapters: dict[str, ProviderAdapter] = {}

        for p in config.providers:
            adapter = get_adapter(p.name)
            self._adapters[p.name] = adapter

            # Build auth headers from adapter
            headers = adapter.translate_auth_header(p.api_key)

            self._clients[p.name] = httpx.AsyncClient(
                base_url=p.base_url.rstrip("/"),
                timeout=httpx.Timeout(p.timeout, connect=10.0),
                headers=headers,
                limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            )

    async def complete(self, provider: ProviderConfig, payload: dict) -> dict:
        """Non-streaming completion with provider-specific translation."""
        client = self._clients[provider.name]
        adapter = self._adapters[provider.name]

        # Translate request to provider's format
        endpoint, body, extra_headers = adapter.translate_request(payload)

        resp = await client.post(endpoint, json=body, headers=extra_headers)
        if resp.status_code != 200:
            raise ProviderError(provider.name, resp.status_code, resp.text[:200])

        # Translate response back to OpenAI format
        raw_response = resp.json()
        return adapter.translate_response(raw_response, payload.get("model", ""))

    async def stream(self, provider: ProviderConfig, payload: dict) -> AsyncGenerator[bytes, None]:
        """Streaming completion — yields raw SSE chunks."""
        client = self._clients[provider.name]
        adapter = self._adapters[provider.name]

        # Translate request
        endpoint, body, extra_headers = adapter.translate_request(payload)
        body["stream"] = True

        async with client.stream("POST", endpoint, json=body, headers=extra_headers) as resp:
            if resp.status_code != 200:
                raw = await resp.aread()
                raise ProviderError(provider.name, resp.status_code, raw.decode()[:200])
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
