"""Continuous eval — detect quality regressions on production traffic.

Samples a configurable % of requests, scores them against reference
metrics (latency, coherence, factuality), and alerts when a model
degrades beyond threshold with statistical significance.
"""
from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class EvalConfig:
    """Configuration for continuous evaluation."""
    sample_rate: float = 0.1            # % of traffic to eval
    window_size: int = 100              # samples per eval window
    alert_threshold_p: float = 0.05     # p-value for significance
    min_samples: int = 30               # minimum before testing
    metrics: list[str] = field(default_factory=lambda: ["latency", "token_efficiency"])


@dataclass
class EvalWindow:
    """A sliding window of eval observations for one model."""
    model: str
    observations: list[dict] = field(default_factory=list)
    baseline_mean: dict[str, float] = field(default_factory=dict)
    baseline_std: dict[str, float] = field(default_factory=dict)
    alerts: list[dict] = field(default_factory=list)


class ContinuousEval:
    """Continuous quality evaluation engine.

    Compares recent traffic metrics against an established baseline.
    Fires alerts when degradation is statistically significant.
    """

    def __init__(self, config: EvalConfig | None = None, alert_callback: Callable | None = None):
        self.config = config or EvalConfig()
        self.alert_callback = alert_callback
        self._windows: dict[str, EvalWindow] = {}
        self._total_sampled = 0

    def should_sample(self) -> bool:
        """Probabilistic sampling gate."""
        import random
        return random.random() < self.config.sample_rate

    def record(self, model: str, metrics: dict):
        """Record an eval observation."""
        if model not in self._windows:
            self._windows[model] = EvalWindow(model=model)

        window = self._windows[model]
        window.observations.append({**metrics, "_t": time.time()})
        self._total_sampled += 1

        # Keep window bounded
        if len(window.observations) > self.config.window_size * 3:
            window.observations = window.observations[-self.config.window_size * 2:]

        # Check for regression
        if len(window.observations) >= self.config.min_samples:
            self._check_regression(window)

    def set_baseline(self, model: str, metric: str, mean: float, std: float):
        """Set a baseline for comparison."""
        if model not in self._windows:
            self._windows[model] = EvalWindow(model=model)
        self._windows[model].baseline_mean[metric] = mean
        self._windows[model].baseline_std[metric] = std

    def auto_baseline(self, model: str):
        """Compute baseline from first window_size observations."""
        window = self._windows.get(model)
        if not window or len(window.observations) < self.config.min_samples:
            return

        baseline_obs = window.observations[:self.config.window_size]
        for metric in self.config.metrics:
            values = [o.get(metric) for o in baseline_obs if o.get(metric) is not None]
            if values:
                mean = sum(values) / len(values)
                std = math.sqrt(sum((x - mean) ** 2 for x in values) / max(len(values) - 1, 1))
                window.baseline_mean[metric] = mean
                window.baseline_std[metric] = std

    def _check_regression(self, window: EvalWindow):
        """Check recent observations against baseline."""
        if not window.baseline_mean:
            self.auto_baseline(window.model)
            return

        recent = window.observations[-self.config.window_size:]
        for metric in self.config.metrics:
            if metric not in window.baseline_mean:
                continue

            values = [o.get(metric) for o in recent if o.get(metric) is not None]
            if len(values) < self.config.min_samples:
                continue

            recent_mean = sum(values) / len(values)
            recent_std = math.sqrt(sum((x - recent_mean) ** 2 for x in values) / max(len(values) - 1, 1))

            # Z-test against baseline
            baseline_mean = window.baseline_mean[metric]
            baseline_std = window.baseline_std[metric]
            se = math.sqrt((baseline_std ** 2 / self.config.window_size) + (recent_std ** 2 / len(values)))

            if se == 0:
                continue

            z = (recent_mean - baseline_mean) / se
            p_value = 2 * (1 - self._normal_cdf(abs(z)))

            if p_value < self.config.alert_threshold_p:
                # Determine direction
                direction = "degraded" if recent_mean > baseline_mean else "improved"
                if metric in ("token_efficiency",):
                    direction = "degraded" if recent_mean < baseline_mean else "improved"

                alert = {
                    "model": window.model,
                    "metric": metric,
                    "direction": direction,
                    "baseline_mean": round(baseline_mean, 4),
                    "current_mean": round(recent_mean, 4),
                    "change_pct": round((recent_mean - baseline_mean) / baseline_mean * 100, 2),
                    "p_value": round(p_value, 4),
                    "z_score": round(z, 3),
                    "sample_size": len(values),
                    "timestamp": time.time(),
                }
                window.alerts.append(alert)

                if self.alert_callback:
                    self.alert_callback(alert)

    def get_status(self) -> dict:
        """Get eval engine status."""
        return {
            "total_sampled": self._total_sampled,
            "models_tracked": len(self._windows),
            "models": {
                name: {
                    "observations": len(w.observations),
                    "baseline_set": bool(w.baseline_mean),
                    "alerts": len(w.alerts),
                    "last_alert": w.alerts[-1] if w.alerts else None,
                }
                for name, w in self._windows.items()
            },
        }

    @staticmethod
    def _normal_cdf(x: float) -> float:
        return 0.5 * (1 + math.erf(x / math.sqrt(2)))
