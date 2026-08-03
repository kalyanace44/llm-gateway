"""LLM backend adapters — uniform interface to vLLM, OpenAI, Anthropic, Ollama."""
from __future__ import annotations

import time
from typing import AsyncIterator

import httpx

from gateway.config import BackendConfig


class BackendError(Exception):
    """Backend request failed."""

    def __init__(self, message: str, status_code: int = 502):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class Backend:
    """Adapter for a single LLM backend (OpenAI-compatible API)."""

    def __init__(self, cfg: BackendConfig):
        self.cfg = cfg
        self.name = cfg.name
        self.provider = cfg.provider
        self.base_url = cfg.base_url.rstrip("/")
        self.models = cfg.models
        self.enabled = cfg.enabled
        self.weight = cfg.weight
        self.priority = cfg.priority
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(cfg.timeout, connect=10.0),
            limits=httpx.Limits(max_connections=cfg.max_concurrent),
        )
        self._headers = {"Content-Type": "application/json"}
        if cfg.api_key:
            self._headers["Authorization"] = f"Bearer {cfg.api_key}"

        # Health state
        self.healthy = True
        self.last_check: float = 0
        self.consecutive_failures: int = 0

    async def chat_completions(self, payload: dict) -> dict:
        """Non-streaming chat completion."""
        try:
            resp = await self._client.post(
                "/chat/completions",
                json=payload,
                headers=self._headers,
            )
            if resp.status_code >= 500:
                self._mark_unhealthy()
                raise BackendError(f"{self.name} returned {resp.status_code}", resp.status_code)
            resp.raise_for_status()
            self._mark_healthy()
            return resp.json()
        except httpx.TimeoutException:
            self._mark_unhealthy()
            raise BackendError(f"{self.name} timed out", 504)
        except httpx.ConnectError:
            self._mark_unhealthy()
            raise BackendError(f"{self.name} connection refused", 503)

    async def chat_completions_stream(self, payload: dict) -> AsyncIterator[bytes]:
        """Streaming chat completion — yields raw SSE bytes."""
        payload["stream"] = True
        try:
            async with self._client.stream(
                "POST",
                "/chat/completions",
                json=payload,
                headers=self._headers,
            ) as resp:
                if resp.status_code >= 500:
                    self._mark_unhealthy()
                    raise BackendError(f"{self.name} returned {resp.status_code}", resp.status_code)
                self._mark_healthy()
                async for chunk in resp.aiter_bytes():
                    yield chunk
        except httpx.TimeoutException:
            self._mark_unhealthy()
            raise BackendError(f"{self.name} timed out", 504)
        except httpx.ConnectError:
            self._mark_unhealthy()
            raise BackendError(f"{self.name} connection refused", 503)

    async def list_models(self) -> list[str]:
        """Fetch available models from backend."""
        if self.models:
            return self.models
        try:
            resp = await self._client.get("/models", headers=self._headers)
            resp.raise_for_status()
            data = resp.json()
            return [m["id"] for m in data.get("data", [])]
        except Exception:
            return self.models

    async def health_check(self) -> bool:
        """Quick health check."""
        try:
            resp = await self._client.get("/models", headers=self._headers, timeout=5.0)
            healthy = resp.status_code < 500
            if healthy:
                self._mark_healthy()
            else:
                self._mark_unhealthy()
            return healthy
        except Exception:
            self._mark_unhealthy()
            return False

    def _mark_healthy(self):
        self.healthy = True
        self.consecutive_failures = 0
        self.last_check = time.time()

    def _mark_unhealthy(self):
        self.consecutive_failures += 1
        if self.consecutive_failures >= 3:
            self.healthy = False
        self.last_check = time.time()

    async def close(self):
        await self._client.aclose()
