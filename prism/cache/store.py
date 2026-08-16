"""Response cache — exact match + semantic similarity with LRU eviction."""
from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from threading import Lock

import numpy as np

from prism.config import CacheConfig


class CacheStore:
    """Thread-safe LRU response cache with optional semantic matching."""

    def __init__(self, config: CacheConfig):
        self.config = config
        self._store: OrderedDict[str, tuple[float, dict]] = OrderedDict()
        self._lock = Lock()
        self._hits = 0
        self._misses = 0
        self._semantic_hits = 0

        # Semantic cache: store embeddings alongside responses
        self._embeddings: OrderedDict[str, np.ndarray] = OrderedDict()
        self._embedding_fn = None  # Set externally if semantic cache enabled

    def set_embedding_fn(self, fn):
        """Set the embedding function for semantic caching.

        fn: callable that takes a string and returns a numpy array (normalized).
        """
        self._embedding_fn = fn

    def get(self, model: str, messages: list) -> dict | None:
        """Check cache for a matching response (exact first, then semantic)."""
        if not self.config.enabled:
            return None

        # 1. Exact match
        key = self._make_key(model, messages)
        with self._lock:
            entry = self._store.get(key)
            if entry is not None:
                timestamp, response = entry
                if time.time() - timestamp > self.config.ttl_seconds:
                    del self._store[key]
                    if key in self._embeddings:
                        del self._embeddings[key]
                    self._misses += 1
                    return None
                self._store.move_to_end(key)
                self._hits += 1
                return response

        # 2. Semantic match (if enabled)
        if self._embedding_fn and self.config.semantic_threshold < 1.0:
            semantic_result = self._semantic_lookup(model, messages)
            if semantic_result is not None:
                with self._lock:
                    self._semantic_hits += 1
                    self._hits += 1
                return semantic_result

        with self._lock:
            self._misses += 1
        return None

    def put(self, model: str, messages: list, response: dict):
        """Cache a response (with embedding if semantic cache enabled)."""
        if not self.config.enabled:
            return

        key = self._make_key(model, messages)
        with self._lock:
            self._store[key] = (time.time(), response)
            self._store.move_to_end(key)

            # Store embedding for semantic matching
            if self._embedding_fn:
                try:
                    text = self._messages_to_text(messages)
                    embedding = self._embedding_fn(text)
                    self._embeddings[key] = embedding
                    self._embeddings.move_to_end(key)
                except (TypeError, ValueError, RuntimeError):
                    pass  # Don't fail caching if embedding fails

            # Evict oldest if over max
            while len(self._store) > self.config.max_entries:
                evicted_key, _ = self._store.popitem(last=False)
                self._embeddings.pop(evicted_key, None)

    def invalidate(self, model: str | None = None):
        """Clear cache (optionally for a specific model)."""
        with self._lock:
            if model is None:
                self._store.clear()
                self._embeddings.clear()
            else:
                keys_to_remove = [k for k in self._store if k.startswith(f"{model}:")]
                for k in keys_to_remove:
                    del self._store[k]
                    self._embeddings.pop(k, None)

    def _semantic_lookup(self, model: str, messages: list) -> dict | None:
        """Find a semantically similar cached response."""
        if not self._embeddings:
            return None

        try:
            text = self._messages_to_text(messages)
            query_embedding = self._embedding_fn(text)
        except (TypeError, ValueError, RuntimeError):
            return None

        best_score = 0.0
        best_key = None

        with self._lock:
            now = time.time()
            for key, emb in self._embeddings.items():
                # Only match same model
                if not key.startswith(f"{model}:"):
                    continue
                # Check TTL
                entry = self._store.get(key)
                if entry is None or now - entry[0] > self.config.ttl_seconds:
                    continue
                # Cosine similarity (embeddings should be normalized)
                score = float(np.dot(query_embedding, emb))
                if score > best_score:
                    best_score = score
                    best_key = key

        if best_key and best_score >= self.config.semantic_threshold:
            with self._lock:
                entry = self._store.get(best_key)
                if entry:
                    self._store.move_to_end(best_key)
                    return entry[1]
        return None

    @property
    def stats(self) -> dict:
        return {
            "size": len(self._store),
            "max_entries": self.config.max_entries,
            "hits": self._hits,
            "misses": self._misses,
            "semantic_hits": self._semantic_hits,
            "hit_rate": round(self._hits / max(self._hits + self._misses, 1), 3),
            "ttl_seconds": self.config.ttl_seconds,
            "semantic_enabled": self._embedding_fn is not None,
            "semantic_threshold": self.config.semantic_threshold,
            "embeddings_cached": len(self._embeddings),
        }

    @staticmethod
    def _make_key(model: str, messages: list) -> str:
        """Deterministic cache key from model + messages."""
        content = json.dumps({"model": model, "messages": [m.model_dump() if hasattr(m, "model_dump") else m for m in messages]}, sort_keys=True)
        return f"{model}:{hashlib.sha256(content.encode()).hexdigest()[:16]}"

    @staticmethod
    def _messages_to_text(messages: list) -> str:
        """Extract searchable text from messages for embedding."""
        parts = []
        for m in messages:
            if hasattr(m, "model_dump"):
                m = m.model_dump()
            content = m.get("content", "")
            if content:
                parts.append(content)
        return " ".join(parts)
