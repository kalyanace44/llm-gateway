"""Circuit breaker registry — per-provider failure isolation."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

from prism.config import ResilienceConfig


class State(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    """Single circuit breaker for one provider."""

    name: str
    config: ResilienceConfig
    state: State = State.CLOSED
    failure_count: int = 0
    success_count: int = 0
    last_failure_time: float = 0.0
    last_state_change: float = field(default_factory=time.time)
    total_trips: int = 0

    def can_execute(self) -> bool:
        if self.state == State.CLOSED:
            return True
        if self.state == State.OPEN:
            if time.time() - self.last_failure_time >= self.config.recovery_timeout_seconds:
                self._transition(State.HALF_OPEN)
                return True
            return False
        if self.state == State.HALF_OPEN:
            return True
        return False

    def record_success(self):
        if self.state == State.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= self.config.success_threshold:
                self._transition(State.CLOSED)
        elif self.state == State.CLOSED:
            self.failure_count = 0

    def record_failure(self):
        self.last_failure_time = time.time()
        if self.state == State.HALF_OPEN:
            self._transition(State.OPEN)
        elif self.state == State.CLOSED:
            self.failure_count += 1
            if self.failure_count >= self.config.failure_threshold:
                self._transition(State.OPEN)

    def reset(self):
        self._transition(State.CLOSED)

    def _transition(self, new_state: State):
        self.state = new_state
        self.last_state_change = time.time()
        if new_state == State.CLOSED:
            self.failure_count = 0
            self.success_count = 0
        elif new_state == State.OPEN:
            self.total_trips += 1
            self.success_count = 0
        elif new_state == State.HALF_OPEN:
            self.success_count = 0

    @property
    def status(self) -> dict:
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "total_trips": self.total_trips,
            "time_in_state_s": round(time.time() - self.last_state_change, 1),
        }


class CircuitBreakerRegistry:
    """Manages circuit breakers for all providers."""

    def __init__(self, config: ResilienceConfig):
        self.config = config
        self._breakers: dict[str, CircuitBreaker] = {}

    def register(self, name: str):
        self._breakers[name] = CircuitBreaker(name=name, config=self.config)

    def can_execute(self, name: str) -> bool:
        cb = self._breakers.get(name)
        return cb.can_execute() if cb else True

    def record_success(self, name: str):
        cb = self._breakers.get(name)
        if cb:
            cb.record_success()

    def record_failure(self, name: str):
        cb = self._breakers.get(name)
        if cb:
            cb.record_failure()

    def reset(self, name: str):
        cb = self._breakers.get(name)
        if cb:
            cb.reset()

    def get_all_status(self) -> list[dict]:
        return [cb.status for cb in self._breakers.values()]

    def get_healthy(self) -> list[str]:
        return [n for n, cb in self._breakers.items() if cb.can_execute()]
