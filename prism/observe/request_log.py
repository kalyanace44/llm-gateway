"""Request log — in-memory ring buffer for request tracing and debugging."""
from __future__ import annotations

import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from threading import Lock


@dataclass
class RequestLog:
    """A single logged request."""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: float = field(default_factory=time.time)
    model: str = ""
    provider: str = ""
    team: str = ""
    status: str = "success"  # success | error | blocked | cached
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cached: bool = False
    error: str = ""
    # Security
    pii_detected: bool = False
    pii_action: str = ""
    entities_found: int = 0
    # Cost
    cost_usd: float = 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "time_ago": _time_ago(self.timestamp),
            "model": self.model,
            "provider": self.provider,
            "team": self.team,
            "status": self.status,
            "latency_ms": round(self.latency_ms, 1),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.input_tokens + self.output_tokens,
            "cached": self.cached,
            "error": self.error,
            "pii_detected": self.pii_detected,
            "pii_action": self.pii_action,
            "entities_found": self.entities_found,
            "cost_usd": round(self.cost_usd, 6),
        }


class RequestLogger:
    """Thread-safe ring buffer for request logs."""

    def __init__(self, max_entries: int = 10_000):
        self._buffer: deque[RequestLog] = deque(maxlen=max_entries)
        self._lock = Lock()
        self._max = max_entries

    def log(self, entry: RequestLog):
        """Add a request to the log."""
        with self._lock:
            self._buffer.append(entry)

    def get_recent(self, limit: int = 50, offset: int = 0,
                   team: str | None = None, model: str | None = None,
                   status: str | None = None, provider: str | None = None) -> list[dict]:
        """Get recent requests with optional filters."""
        with self._lock:
            entries = list(reversed(self._buffer))

        # Apply filters
        if team:
            entries = [e for e in entries if e.team == team]
        if model:
            entries = [e for e in entries if e.model == model]
        if status:
            entries = [e for e in entries if e.status == status]
        if provider:
            entries = [e for e in entries if e.provider == provider]

        # Paginate
        entries = entries[offset:offset + limit]
        return [e.to_dict() for e in entries]

    def get_summary(self) -> dict:
        """Get summary statistics from the log buffer."""
        with self._lock:
            entries = list(self._buffer)

        if not entries:
            return {
                "total_logged": 0,
                "buffer_size": self._max,
                "time_range_seconds": 0,
            }

        total = len(entries)
        successes = sum(1 for e in entries if e.status == "success")
        errors = sum(1 for e in entries if e.status == "error")
        cached = sum(1 for e in entries if e.cached)
        blocked = sum(1 for e in entries if e.status == "blocked")

        latencies = [e.latency_ms for e in entries if e.latency_ms > 0]
        costs = [e.cost_usd for e in entries if e.cost_usd > 0]

        return {
            "total_logged": total,
            "buffer_size": self._max,
            "time_range_seconds": round(entries[-1].timestamp - entries[0].timestamp, 1) if len(entries) > 1 else 0,
            "success_count": successes,
            "error_count": errors,
            "cached_count": cached,
            "blocked_count": blocked,
            "error_rate": round(errors / max(total, 1), 4),
            "cache_rate": round(cached / max(total, 1), 4),
            "avg_latency_ms": round(sum(latencies) / max(len(latencies), 1), 1),
            "p95_latency_ms": round(sorted(latencies)[int(len(latencies) * 0.95)] if latencies else 0, 1),
            "total_cost_usd": round(sum(costs), 4),
            "top_models": _top_n(entries, "model"),
            "top_teams": _top_n(entries, "team"),
            "top_providers": _top_n(entries, "provider"),
            "top_errors": _top_n([e for e in entries if e.error], "error", n=5),
        }

    @property
    def size(self) -> int:
        return len(self._buffer)


def _time_ago(timestamp: float) -> str:
    """Human-readable time ago."""
    diff = time.time() - timestamp
    if diff < 60:
        return f"{int(diff)}s ago"
    if diff < 3600:
        return f"{int(diff / 60)}m ago"
    if diff < 86400:
        return f"{int(diff / 3600)}h ago"
    return f"{int(diff / 86400)}d ago"


def _top_n(entries: list, attr: str, n: int = 5) -> list[dict]:
    """Get top N values for an attribute."""
    counts: dict[str, int] = {}
    for e in entries:
        val = getattr(e, attr, "")
        if val:
            counts[val] = counts.get(val, 0) + 1
    sorted_items = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:n]
    return [{"value": k, "count": v} for k, v in sorted_items]
