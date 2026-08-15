"""Metrics collection — Prometheus-compatible + internal counters."""
from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from threading import Lock


@dataclass
class RequestMetric:
    """A single request metric point."""
    model: str
    provider: str
    team: str
    latency: float
    input_tokens: int = 0
    output_tokens: int = 0
    cached: bool = False
    error: str = ""
    timestamp: float = field(default_factory=time.time)


class MetricsCollector:
    """Collects request metrics for observability."""

    def __init__(self):
        self._lock = Lock()
        self._total_requests = 0
        self._total_errors = 0
        self._total_tokens = 0
        self._total_cache_hits = 0
        self._by_model: dict[str, int] = defaultdict(int)
        self._by_provider: dict[str, int] = defaultdict(int)
        self._by_team: dict[str, int] = defaultdict(int)
        self._latencies: list[float] = []
        self._cost_by_team: dict[str, float] = defaultdict(float)

    def record_request(
        self, model: str, provider: str, team: str,
        latency: float, input_tokens: int = 0,
        output_tokens: int = 0, stream: bool = False,
    ):
        """Record a successful request."""
        with self._lock:
            self._total_requests += 1
            self._total_tokens += input_tokens + output_tokens
            self._by_model[model] += 1
            self._by_provider[provider] += 1
            self._by_team[team] += 1
            self._latencies.append(latency)
            # Keep last 10K latencies
            if len(self._latencies) > 10000:
                self._latencies = self._latencies[-5000:]

    def record_error(self, model: str, provider: str, team: str, error: str):
        """Record a failed request."""
        with self._lock:
            self._total_errors += 1
            self._by_provider[f"{provider}:error"] += 1

    def record_cache_hit(self, model: str, team: str):
        """Record a cache hit."""
        with self._lock:
            self._total_cache_hits += 1
            self._total_requests += 1
            self._by_model[model] += 1
            self._by_team[team] += 1

    def record_cost(self, team: str, cost_usd: float):
        """Record cost attribution."""
        with self._lock:
            self._cost_by_team[team] += cost_usd

    @property
    def summary(self) -> dict:
        """Current metrics summary."""
        with self._lock:
            latencies = sorted(self._latencies) if self._latencies else [0]
            return {
                "total_requests": self._total_requests,
                "total_errors": self._total_errors,
                "total_tokens": self._total_tokens,
                "total_cache_hits": self._total_cache_hits,
                "error_rate": round(self._total_errors / max(self._total_requests, 1), 4),
                "cache_hit_rate": round(self._total_cache_hits / max(self._total_requests, 1), 4),
                "latency_p50": round(latencies[len(latencies) // 2], 3),
                "latency_p95": round(latencies[int(len(latencies) * 0.95)], 3) if len(latencies) > 1 else 0,
                "latency_p99": round(latencies[int(len(latencies) * 0.99)], 3) if len(latencies) > 1 else 0,
                "by_model": dict(self._by_model),
                "by_provider": dict(self._by_provider),
                "by_team": dict(self._by_team),
                "cost_by_team": {k: round(v, 4) for k, v in self._cost_by_team.items()},
            }

    def prometheus_text(self) -> str:
        """Export metrics in Prometheus text format."""
        lines = []
        lines.append("# HELP prism_requests_total Total requests proxied")
        lines.append("# TYPE prism_requests_total counter")
        lines.append(f"prism_requests_total {self._total_requests}")
        lines.append("# HELP prism_errors_total Total errors")
        lines.append("# TYPE prism_errors_total counter")
        lines.append(f"prism_errors_total {self._total_errors}")
        lines.append("# HELP prism_tokens_total Total tokens processed")
        lines.append("# TYPE prism_tokens_total counter")
        lines.append(f"prism_tokens_total {self._total_tokens}")
        lines.append("# HELP prism_cache_hits_total Cache hits")
        lines.append("# TYPE prism_cache_hits_total counter")
        lines.append(f"prism_cache_hits_total {self._total_cache_hits}")

        for model, count in self._by_model.items():
            lines.append(f'prism_requests_by_model{{model="{model}"}} {count}')
        for provider, count in self._by_provider.items():
            lines.append(f'prism_requests_by_provider{{provider="{provider}"}} {count}')
        for team, count in self._by_team.items():
            lines.append(f'prism_requests_by_team{{team="{team}"}} {count}')

        return "\n".join(lines) + "\n"
