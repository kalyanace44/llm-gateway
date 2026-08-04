"""Response cache — exact match + LRU eviction."""
from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from threading import Lock

from prism.config import CacheConfig


class CacheStore:
    """Thread-safe LRU response cache."""

    def __init__(self, config: CacheConfig):
        self.config = config
        self._store: OrderedDict[str, tuple[float, dict]] = OrderedDict()
        self._lock = Lock()
        self._hits = 0
        self._misses = 0

    def get(self, model: str, messages: list) -> dict | None:
        """Check cache for a matching response."""
        if not self.config.enabled:
            return None

        key = self._make_key(model, messages)
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return None

            timestamp, response = entry
            # Check TTL
            if time.time() - timestamp > self.config.ttl_seconds:
                del self._store[key]
                self._misses += 1
                return None

            # Move to end (LRU)
            self._store.move_to_end(key)
            self._hits += 1
            return response

    def put(self, model: str, messages: list, response: dict):
        """Cache a response."""
        if not self.config.enabled:
            return

        key = self._make_key(model, messages)
        with self._lock:
            self._store[key] = (time.time(), response)
            self._store.move_to_end(key)

            # Evict oldest if over max
            while len(self._store) > self.config.max_entries:
                self._store.popitem(last=False)

    def invalidate(self, model: str | None = None):
        """Clear cache (optionally for a specific model)."""
        with self._lock:
            if model is None:
                self._store.clear()
            else:
                keys_to_remove = [k for k in self._store if k.startswith(f"{model}:")]
                for k in keys_to_remove:
                    del self._store[k]

    @property
    def stats(self) -> dict:
        return {
            "size": len(self._store),
            "max_entries": self.config.max_entries,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / max(self._hits + self._misses, 1), 3),
            "ttl_seconds": self.config.ttl_seconds,
        }

    @staticmethod
    def _make_key(model: str, messages: list) -> str:
        """Deterministic cache key from model + messages."""
        content = json.dumps({"model": model, "messages": [m.model_dump() if hasattr(m, "model_dump") else m for m in messages]}, sort_keys=True)
        return f"{model}:{hashlib.sha256(content.encode()).hexdigest()[:16]}"
