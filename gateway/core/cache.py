"""Semantic response cache — deduplicates similar requests."""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CacheEntry:
    """A cached response."""

    key: str
    response: dict
    model: str
    created_at: float
    ttl: int
    hits: int = 0


class ResponseCache:
    """
    In-memory response cache with TTL.
    Caches based on model + messages hash (exact match).
    For production: swap to Redis with semantic similarity.
    """

    def __init__(self, ttl: int = 3600, max_size: int = 10_000):
        self.ttl = ttl
        self.max_size = max_size
        self._cache: dict[str, CacheEntry] = {}
        self._hits = 0
        self._misses = 0

    @staticmethod
    def _hash_request(model: str, messages: list[dict]) -> str:
        """Create a cache key from model + messages."""
        content = json.dumps({"model": model, "messages": messages}, sort_keys=True)
        return hashlib.sha256(content.encode()).hexdigest()[:32]

    def get(self, model: str, messages: list[dict]) -> Optional[dict]:
        """Look up a cached response. Returns None on miss."""
        key = self._hash_request(model, messages)
        entry = self._cache.get(key)

        if entry is None:
            self._misses += 1
            return None

        # Check TTL
        if time.time() - entry.created_at > entry.ttl:
            del self._cache[key]
            self._misses += 1
            return None

        entry.hits += 1
        self._hits += 1
        return entry.response

    def put(self, model: str, messages: list[dict], response: dict):
        """Store a response in cache."""
        # Evict oldest if at capacity
        if len(self._cache) >= self.max_size:
            oldest_key = min(self._cache, key=lambda k: self._cache[k].created_at)
            del self._cache[oldest_key]

        key = self._hash_request(model, messages)
        self._cache[key] = CacheEntry(
            key=key,
            response=response,
            model=model,
            created_at=time.time(),
            ttl=self.ttl,
        )

    def invalidate(self, model: str, messages: list[dict]):
        """Remove a specific entry."""
        key = self._hash_request(model, messages)
        self._cache.pop(key, None)

    def clear(self):
        """Clear all cached entries."""
        self._cache.clear()

    @property
    def stats(self) -> dict:
        """Cache statistics."""
        total = self._hits + self._misses
        return {
            "size": len(self._cache),
            "max_size": self.max_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / max(total, 1), 3),
            "ttl": self.ttl,
        }
