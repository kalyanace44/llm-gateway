"""Cost tracking — per-request token counting and cost attribution."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import tiktoken


# Default pricing per 1K tokens (USD)
DEFAULT_COSTS: dict[str, tuple[float, float]] = {
    # (input_per_1k, output_per_1k)
    "gpt-4o": (0.0025, 0.01),
    "gpt-4o-mini": (0.00015, 0.0006),
    "gpt-4-turbo": (0.01, 0.03),
    "claude-sonnet-4-20250514": (0.003, 0.015),
    "claude-3-5-haiku-20241022": (0.001, 0.005),
    "claude-sonnet-4.5": (0.003, 0.015),
    "llama-3.1-70b": (0.0, 0.0),  # Self-hosted
    "llama-3.1-8b": (0.0, 0.0),
}


@dataclass
class UsageRecord:
    """Single request usage record."""

    timestamp: float
    api_key_hash: str
    model: str
    backend: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_usd: float
    latency_ms: float
    ttft_ms: float = 0.0  # Time to first token
    cached: bool = False
    status: str = "success"  # success | error | timeout


@dataclass
class UsageTracker:
    """Tracks token usage and costs."""

    cost_table: dict[str, tuple[float, float]] = field(default_factory=lambda: dict(DEFAULT_COSTS))
    records: list[UsageRecord] = field(default_factory=list)
    _key_totals: dict[str, dict] = field(default_factory=dict)

    def count_tokens(self, text: str, model: str = "gpt-4o") -> int:
        """Count tokens in text using tiktoken."""
        try:
            enc = tiktoken.encoding_for_model(model)
        except KeyError:
            # Fallback to cl100k_base for unknown models
            enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))

    def count_messages_tokens(self, messages: list[dict], model: str = "gpt-4o") -> int:
        """Count tokens in a chat messages list."""
        total = 0
        for msg in messages:
            total += 4  # message overhead
            content = msg.get("content", "")
            if isinstance(content, str):
                total += self.count_tokens(content, model)
            elif isinstance(content, list):
                for part in content:
                    if part.get("type") == "text":
                        total += self.count_tokens(part.get("text", ""), model)
        total += 2  # reply priming
        return total

    def calculate_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """Calculate cost in USD."""
        costs = self.cost_table.get(model, (0.0, 0.0))
        input_cost = (input_tokens / 1000.0) * costs[0]
        output_cost = (output_tokens / 1000.0) * costs[1]
        return round(input_cost + output_cost, 6)

    def record(
        self,
        api_key_hash: str,
        model: str,
        backend: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: float,
        ttft_ms: float = 0.0,
        cached: bool = False,
        status: str = "success",
    ) -> UsageRecord:
        """Record a request's usage."""
        cost = self.calculate_cost(model, input_tokens, output_tokens)
        rec = UsageRecord(
            timestamp=time.time(),
            api_key_hash=api_key_hash,
            model=model,
            backend=backend,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            cost_usd=cost,
            latency_ms=latency_ms,
            ttft_ms=ttft_ms,
            cached=cached,
            status=status,
        )
        self.records.append(rec)

        # Update per-key totals
        if api_key_hash not in self._key_totals:
            self._key_totals[api_key_hash] = {
                "requests": 0,
                "total_tokens": 0,
                "total_cost": 0.0,
            }
        totals = self._key_totals[api_key_hash]
        totals["requests"] += 1
        totals["total_tokens"] += input_tokens + output_tokens
        totals["total_cost"] += cost

        return rec

    def get_key_usage(self, api_key_hash: str) -> dict:
        """Get usage summary for an API key."""
        return self._key_totals.get(api_key_hash, {
            "requests": 0,
            "total_tokens": 0,
            "total_cost": 0.0,
        })

    def get_summary(self, since: float = 0) -> dict:
        """Get usage summary since a timestamp."""
        relevant = [r for r in self.records if r.timestamp >= since]
        if not relevant:
            return {"requests": 0, "tokens": 0, "cost_usd": 0.0}
        return {
            "requests": len(relevant),
            "tokens": sum(r.total_tokens for r in relevant),
            "cost_usd": round(sum(r.cost_usd for r in relevant), 4),
            "avg_latency_ms": round(sum(r.latency_ms for r in relevant) / len(relevant), 1),
            "error_rate": round(sum(1 for r in relevant if r.status != "success") / len(relevant), 3),
        }
