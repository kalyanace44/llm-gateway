"""Rate limiting — token bucket algorithm backed by Redis or in-memory."""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class TokenBucket:
    """In-memory token bucket for rate limiting."""

    capacity: float
    refill_rate: float  # tokens per second
    tokens: float = field(init=False)
    last_refill: float = field(init=False)

    def __post_init__(self):
        self.tokens = self.capacity
        self.last_refill = time.time()

    def _refill(self):
        now = time.time()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now

    def consume(self, amount: float = 1.0) -> bool:
        """Try to consume tokens. Returns True if allowed, False if rate limited."""
        self._refill()
        if self.tokens >= amount:
            self.tokens -= amount
            return True
        return False

    @property
    def wait_time(self) -> float:
        """Seconds until 1 token is available."""
        self._refill()
        if self.tokens >= 1:
            return 0.0
        return (1.0 - self.tokens) / self.refill_rate


class RateLimiter:
    """Per-key rate limiting using token buckets."""

    def __init__(self, default_rpm: int = 60, default_tpm: int = 100_000):
        self.default_rpm = default_rpm
        self.default_tpm = default_tpm
        self._request_buckets: dict[str, TokenBucket] = {}
        self._token_buckets: dict[str, TokenBucket] = {}

    def _get_request_bucket(self, key: str, rpm: int | None = None) -> TokenBucket:
        rpm = rpm or self.default_rpm
        if key not in self._request_buckets:
            # Bucket: capacity = rpm * burst_multiplier, refill = rpm / 60 per second
            self._request_buckets[key] = TokenBucket(
                capacity=rpm * 1.5,
                refill_rate=rpm / 60.0,
            )
        return self._request_buckets[key]

    def _get_token_bucket(self, key: str, tpm: int | None = None) -> TokenBucket:
        tpm = tpm or self.default_tpm
        if key not in self._token_buckets:
            self._token_buckets[key] = TokenBucket(
                capacity=tpm * 1.5,
                refill_rate=tpm / 60.0,
            )
        return self._token_buckets[key]

    def check_request(self, key: str, rpm: int | None = None) -> tuple[bool, float]:
        """
        Check if a request is allowed.
        Returns (allowed, retry_after_seconds).
        """
        bucket = self._get_request_bucket(key, rpm)
        if bucket.consume(1.0):
            return True, 0.0
        return False, bucket.wait_time

    def check_tokens(self, key: str, token_count: int, tpm: int | None = None) -> tuple[bool, float]:
        """
        Check if token usage is allowed.
        Returns (allowed, retry_after_seconds).
        """
        bucket = self._get_token_bucket(key, tpm)
        if bucket.consume(float(token_count)):
            return True, 0.0
        return False, bucket.wait_time

    def record_tokens(self, key: str, token_count: int):
        """Record tokens consumed after response (for post-request accounting)."""
        bucket = self._get_token_bucket(key)
        bucket.consume(float(token_count))
