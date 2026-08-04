"""Self-healing infrastructure — circuit breakers, health monitoring, drift detection."""
from __future__ import annotations

import time
import enum
from dataclasses import dataclass, field
from typing import Optional
from collections import deque


class CircuitState(enum.Enum):
    """Circuit breaker states."""
    CLOSED = "closed"       # Normal operation
    OPEN = "open"           # Failing, rejecting requests
    HALF_OPEN = "half_open" # Testing if backend recovered


@dataclass
class CircuitBreakerConfig:
    """Configuration for a circuit breaker."""
    failure_threshold: int = 5       # Consecutive failures to trip
    recovery_timeout: float = 60.0   # Seconds in OPEN before trying HALF_OPEN
    success_threshold: int = 3       # Successes in HALF_OPEN to close
    half_open_max_calls: int = 3     # Max concurrent calls in HALF_OPEN


@dataclass
class CircuitBreaker:
    """Per-backend circuit breaker with state machine."""

    name: str
    config: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    success_count: int = 0
    last_failure_time: float = 0.0
    last_state_change: float = field(default_factory=time.time)
    half_open_calls: int = 0
    total_failures: int = 0
    total_successes: int = 0
    total_trips: int = 0

    def can_execute(self) -> bool:
        """Check if a request can proceed."""
        if self.state == CircuitState.CLOSED:
            return True
        elif self.state == CircuitState.OPEN:
            # Check if recovery timeout has elapsed
            if time.time() - self.last_failure_time >= self.config.recovery_timeout:
                self._transition(CircuitState.HALF_OPEN)
                return True
            return False
        elif self.state == CircuitState.HALF_OPEN:
            # Allow limited calls in half-open
            return self.half_open_calls < self.config.half_open_max_calls
        return False

    def record_success(self):
        """Record a successful call."""
        self.total_successes += 1

        if self.state == CircuitState.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= self.config.success_threshold:
                self._transition(CircuitState.CLOSED)
        elif self.state == CircuitState.CLOSED:
            # Reset failure count on success
            self.failure_count = 0

    def record_failure(self):
        """Record a failed call."""
        self.total_failures += 1
        self.last_failure_time = time.time()

        if self.state == CircuitState.HALF_OPEN:
            # Immediately trip back to OPEN
            self._transition(CircuitState.OPEN)
        elif self.state == CircuitState.CLOSED:
            self.failure_count += 1
            if self.failure_count >= self.config.failure_threshold:
                self._transition(CircuitState.OPEN)

    def reset(self):
        """Force reset to CLOSED."""
        self._transition(CircuitState.CLOSED)

    def _transition(self, new_state: CircuitState):
        """Transition to a new state."""
        old_state = self.state
        self.state = new_state
        self.last_state_change = time.time()

        if new_state == CircuitState.CLOSED:
            self.failure_count = 0
            self.success_count = 0
            self.half_open_calls = 0
        elif new_state == CircuitState.OPEN:
            self.total_trips += 1
            self.success_count = 0
            self.half_open_calls = 0
        elif new_state == CircuitState.HALF_OPEN:
            self.success_count = 0
            self.half_open_calls = 0

    @property
    def status(self) -> dict:
        """Current circuit breaker status."""
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "total_failures": self.total_failures,
            "total_successes": self.total_successes,
            "total_trips": self.total_trips,
            "last_failure": self.last_failure_time,
            "time_in_state": round(time.time() - self.last_state_change, 1),
        }


# --- Latency Monitor ---

@dataclass
class LatencyWindow:
    """Sliding window latency tracker."""

    window_size: int = 100
    _samples: deque = field(default_factory=lambda: deque(maxlen=100))

    def record(self, latency: float):
        """Record a latency sample."""
        self._samples.append((time.time(), latency))

    @property
    def p50(self) -> float:
        if not self._samples:
            return 0.0
        latencies = sorted(s[1] for s in self._samples)
        return latencies[len(latencies) // 2]

    @property
    def p95(self) -> float:
        if not self._samples:
            return 0.0
        latencies = sorted(s[1] for s in self._samples)
        idx = int(len(latencies) * 0.95)
        return latencies[min(idx, len(latencies) - 1)]

    @property
    def p99(self) -> float:
        if not self._samples:
            return 0.0
        latencies = sorted(s[1] for s in self._samples)
        idx = int(len(latencies) * 0.99)
        return latencies[min(idx, len(latencies) - 1)]

    @property
    def mean(self) -> float:
        if not self._samples:
            return 0.0
        return sum(s[1] for s in self._samples) / len(self._samples)

    @property
    def count(self) -> int:
        return len(self._samples)

    def recent(self, seconds: int = 300) -> list[float]:
        """Get latencies from the last N seconds."""
        cutoff = time.time() - seconds
        return [s[1] for s in self._samples if s[0] >= cutoff]


# --- Health Monitor ---

@dataclass
class HealthScore:
    """Composite health score for a backend."""

    backend_name: str
    is_healthy: bool = True
    health_score: float = 1.0  # 0.0 to 1.0
    circuit_breaker: Optional[CircuitBreaker] = None
    latency: Optional[LatencyWindow] = None
    last_check: float = field(default_factory=time.time)
    consecutive_failures: int = 0
    error_rate_5m: float = 0.0
    _recent_results: deque = field(default_factory=lambda: deque(maxlen=50))

    def record_result(self, success: bool, latency: float = 0.0):
        """Record a health check or request result."""
        self._recent_results.append((time.time(), success, latency))
        if self.latency:
            self.latency.record(latency)

        if success:
            self.consecutive_failures = 0
            if self.circuit_breaker:
                self.circuit_breaker.record_success()
        else:
            self.consecutive_failures += 1
            if self.circuit_breaker:
                self.circuit_breaker.record_failure()

        self._recalculate()

    def _recalculate(self):
        """Recalculate health score from recent results."""
        if not self._recent_results:
            self.health_score = 1.0
            self.is_healthy = True
            return

        # Error rate over last 5 minutes
        cutoff = time.time() - 300
        recent = [(t, s, l) for t, s, l in self._recent_results if t >= cutoff]
        if recent:
            successes = sum(1 for _, s, _ in recent if s)
            self.error_rate_5m = 1.0 - (successes / len(recent))
        else:
            self.error_rate_5m = 0.0

        # Composite score: error rate + latency drift
        error_penalty = self.error_rate_5m * 0.7
        latency_penalty = 0.0
        if self.latency and self.latency.p99 > 30.0:  # 30s = very slow
            latency_penalty = min(0.3, (self.latency.p99 - 10.0) / 60.0)

        self.health_score = max(0.0, 1.0 - error_penalty - latency_penalty)
        self.is_healthy = self.health_score > 0.3 and self.consecutive_failures < 5

    @property
    def status(self) -> dict:
        """Current health status."""
        return {
            "backend": self.backend_name,
            "healthy": self.is_healthy,
            "health_score": round(self.health_score, 3),
            "error_rate_5m": round(self.error_rate_5m, 3),
            "consecutive_failures": self.consecutive_failures,
            "latency_p50": round(self.latency.p50, 3) if self.latency else 0,
            "latency_p95": round(self.latency.p95, 3) if self.latency else 0,
            "latency_p99": round(self.latency.p99, 3) if self.latency else 0,
            "circuit_breaker": self.circuit_breaker.status if self.circuit_breaker else None,
            "last_check": self.last_check,
        }


# --- Self-Healing Manager ---

class SelfHealingManager:
    """Manages circuit breakers, health monitoring, and auto-recovery for all backends."""

    def __init__(self, config: CircuitBreakerConfig | None = None):
        self.config = config or CircuitBreakerConfig()
        self._backends: dict[str, HealthScore] = {}
        self._actions_log: deque = deque(maxlen=100)

    def register_backend(self, name: str):
        """Register a backend for monitoring."""
        cb = CircuitBreaker(name=name, config=self.config)
        latency = LatencyWindow(window_size=100)
        self._backends[name] = HealthScore(
            backend_name=name,
            circuit_breaker=cb,
            latency=latency,
        )

    def can_route_to(self, name: str) -> bool:
        """Check if a backend is available for routing."""
        health = self._backends.get(name)
        if not health:
            return True  # Unknown backend, allow
        if not health.circuit_breaker:
            return health.is_healthy
        return health.circuit_breaker.can_execute() and health.is_healthy

    def record_success(self, name: str, latency: float):
        """Record a successful request to a backend."""
        health = self._backends.get(name)
        if health:
            health.record_result(success=True, latency=latency)

    def record_failure(self, name: str, latency: float = 0.0, error: str = ""):
        """Record a failed request to a backend."""
        health = self._backends.get(name)
        if health:
            health.record_result(success=False, latency=latency)
            # Log recovery action if circuit tripped
            if health.circuit_breaker and health.circuit_breaker.state == CircuitState.OPEN:
                self._log_action(name, "circuit_opened", f"Backend tripped after failures: {error}")

    def reset_backend(self, name: str) -> bool:
        """Force reset a backend's circuit breaker."""
        health = self._backends.get(name)
        if health and health.circuit_breaker:
            health.circuit_breaker.reset()
            health.consecutive_failures = 0
            health.is_healthy = True
            health.health_score = 1.0
            self._log_action(name, "manual_reset", "Circuit breaker force reset")
            return True
        return False

    def get_healthy_backends(self) -> list[str]:
        """Get list of healthy backends."""
        return [
            name for name, health in self._backends.items()
            if self.can_route_to(name)
        ]

    def get_backend_status(self, name: str) -> dict | None:
        """Get health status for a backend."""
        health = self._backends.get(name)
        return health.status if health else None

    def get_all_status(self) -> list[dict]:
        """Get health status for all backends."""
        return [health.status for health in self._backends.values()]

    def get_actions_log(self, limit: int = 20) -> list[dict]:
        """Get recent self-healing actions."""
        actions = list(self._actions_log)[-limit:]
        actions.reverse()
        return actions

    def _log_action(self, backend: str, action: str, detail: str):
        """Log a self-healing action."""
        self._actions_log.append({
            "timestamp": time.time(),
            "backend": backend,
            "action": action,
            "detail": detail,
        })
