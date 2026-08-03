"""Router — model selection, A/B testing, and fallback chains."""
from __future__ import annotations

import random
from typing import Optional

from gateway.config import GatewayConfig
from gateway.core.backends import Backend, BackendError


class Router:
    """Routes requests to the right backend based on model, weights, and health."""

    def __init__(self, backends: list[Backend], config: GatewayConfig):
        self.backends = backends
        self.config = config
        # Index: model_name -> list of backends that serve it
        self._model_index: dict[str, list[Backend]] = {}
        self._rebuild_index()

    def _rebuild_index(self):
        """Build model → backends lookup."""
        self._model_index.clear()
        for b in self.backends:
            if not b.enabled:
                continue
            for model in b.models:
                self._model_index.setdefault(model, []).append(b)

    def resolve_backend(self, model: str) -> Backend:
        """
        Pick a backend for the given model.
        Uses weighted random selection among healthy backends.
        """
        candidates = self._model_index.get(model, [])
        healthy = [b for b in candidates if b.healthy]

        if not healthy:
            # Try all candidates regardless of health as last resort
            if candidates:
                return candidates[0]
            raise BackendError(f"No backend available for model: {model}", 503)

        if len(healthy) == 1:
            return healthy[0]

        # Weighted random selection (A/B routing)
        weights = [b.weight for b in healthy]
        return random.choices(healthy, weights=weights, k=1)[0]

    def get_fallback_chain(self, model: str) -> list[Backend]:
        """
        Get ordered fallback backends for a model.
        Sorted by priority (highest first), then healthy first.
        """
        candidates = self._model_index.get(model, [])
        return sorted(
            candidates,
            key=lambda b: (-b.priority, not b.healthy),
        )

    async def route_with_fallback(self, model: str, payload: dict, stream: bool = False):
        """
        Try backends in fallback order until one succeeds.
        Returns (backend, response) tuple.
        """
        chain = self.get_fallback_chain(model)
        if not chain:
            raise BackendError(f"No backend available for model: {model}", 503)

        last_error: Optional[BackendError] = None
        for backend in chain:
            try:
                if stream:
                    return backend, backend.chat_completions_stream(payload)
                else:
                    response = await backend.chat_completions(payload)
                    return backend, response
            except BackendError as e:
                last_error = e
                continue

        raise last_error or BackendError("All backends failed", 502)

    def available_models(self) -> list[dict]:
        """List all available models across backends."""
        models = []
        seen = set()
        for model_name, backends in self._model_index.items():
            if model_name in seen:
                continue
            seen.add(model_name)
            healthy_count = sum(1 for b in backends if b.healthy)
            models.append({
                "id": model_name,
                "object": "model",
                "owned_by": backends[0].provider if backends else "unknown",
                "backends": len(backends),
                "healthy_backends": healthy_count,
            })
        return models

    def register_backend(self, backend: Backend):
        """Add a backend at runtime."""
        self.backends.append(backend)
        self._rebuild_index()

    def deregister_backend(self, name: str):
        """Remove a backend at runtime."""
        self.backends = [b for b in self.backends if b.name != name]
        self._rebuild_index()
