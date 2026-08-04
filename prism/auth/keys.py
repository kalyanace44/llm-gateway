"""API key management — teams, rate limits, budget enforcement."""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from threading import Lock

from prism.config import PrismConfig, RateLimitConfig


@dataclass
class KeyInfo:
    """Metadata for an API key."""
    key_hash: str
    name: str
    team: str = "default"
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    created_at: float = field(default_factory=time.time)
    # Usage tracking
    total_requests: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    budget_usd_per_day: float = 0.0  # 0 = unlimited


@dataclass
class _TokenBucket:
    """Token bucket for rate limiting."""
    capacity: float
    tokens: float
    refill_rate: float  # tokens per second
    last_refill: float = field(default_factory=time.time)

    def consume(self, tokens: int = 1) -> bool:
        now = time.time()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now
        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False


class KeyManager:
    """Manages API keys, rate limiting, and usage tracking."""

    def __init__(self, config: PrismConfig):
        self.config = config
        self._keys: dict[str, KeyInfo] = {}
        self._buckets: dict[str, _TokenBucket] = {}
        self._lock = Lock()

        # Register admin key
        if config.admin_key:
            self.register_key(config.admin_key, "admin", team="admin",
                             rate_limit=RateLimitConfig(requests_per_minute=10000))

    def register_key(
        self, raw_key: str, name: str, team: str = "default",
        rate_limit: RateLimitConfig | None = None,
        budget_usd_per_day: float = 0.0,
    ) -> KeyInfo:
        """Register an API key."""
        key_hash = self._hash(raw_key)
        rl = rate_limit or self.config.rate_limit
        info = KeyInfo(
            key_hash=key_hash, name=name, team=team,
            rate_limit=rl, budget_usd_per_day=budget_usd_per_day,
        )
        with self._lock:
            self._keys[key_hash] = info
            self._buckets[key_hash] = _TokenBucket(
                capacity=rl.requests_per_minute,
                tokens=rl.requests_per_minute,
                refill_rate=rl.requests_per_minute / 60.0,
            )
        return info

    def validate(self, raw_key: str) -> KeyInfo | None:
        """Validate a key and return info, or None if invalid."""
        key_hash = self._hash(raw_key)
        return self._keys.get(key_hash)

    def check_rate_limit(self, key_info: KeyInfo) -> bool:
        """Check if request is within rate limit."""
        bucket = self._buckets.get(key_info.key_hash)
        if not bucket:
            return False
        return bucket.consume(1)

    def record_usage(self, key_info: KeyInfo, tokens: int = 0, cost_usd: float = 0.0):
        """Record usage for a key."""
        key_info.total_requests += 1
        key_info.total_tokens += tokens
        key_info.total_cost_usd += cost_usd

    def revoke_key(self, raw_key: str) -> bool:
        """Revoke an API key."""
        key_hash = self._hash(raw_key)
        with self._lock:
            if key_hash in self._keys:
                del self._keys[key_hash]
                self._buckets.pop(key_hash, None)
                return True
        return False

    def list_keys(self) -> list[dict]:
        """List all registered keys (no secrets)."""
        return [
            {
                "name": k.name,
                "team": k.team,
                "total_requests": k.total_requests,
                "total_tokens": k.total_tokens,
                "total_cost_usd": round(k.total_cost_usd, 4),
            }
            for k in self._keys.values()
        ]

    @staticmethod
    def _hash(raw_key: str) -> str:
        return hashlib.sha256(raw_key.encode()).hexdigest()[:16]
