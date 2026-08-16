"""Cost optimizer — recommend cheaper models that meet quality thresholds.

Analyzes traffic patterns and quality scores to suggest model substitutions
that reduce spend without degrading user experience.
"""
from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field

# Approximate pricing per 1M tokens (input/output)
MODEL_PRICING = {
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4-turbo": {"input": 10.00, "output": 30.00},
    "gpt-3.5-turbo": {"input": 0.50, "output": 1.50},
    "claude-sonnet-4-20250514": {"input": 3.00, "output": 15.00},
    "claude-3-5-haiku-20241022": {"input": 0.80, "output": 4.00},
    "claude-3-opus-20240229": {"input": 15.00, "output": 75.00},
    "gemini-1.5-pro": {"input": 1.25, "output": 5.00},
    "gemini-1.5-flash": {"input": 0.075, "output": 0.30},
}


@dataclass
class UsageRecord:
    """Usage record for cost analysis."""
    model: str
    team: str
    input_tokens: int
    output_tokens: int
    latency: float
    quality_score: float = 1.0  # 0-1, from eval
    timestamp: float = field(default_factory=time.time)


@dataclass
class Recommendation:
    """A cost optimization recommendation."""
    current_model: str
    recommended_model: str
    team: str
    current_cost_per_day: float
    projected_cost_per_day: float
    savings_pct: float
    quality_impact: str  # "none", "minimal", "moderate"
    confidence: float
    reason: str


class CostOptimizer:
    """Analyzes usage patterns and recommends cheaper model alternatives."""

    def __init__(self):
        self._records: list[UsageRecord] = []
        self._by_team_model: dict[str, list[UsageRecord]] = defaultdict(list)
        self._max_records = 100_000

    def record(self, usage: UsageRecord):
        """Record a usage observation."""
        self._records.append(usage)
        self._by_team_model[f"{usage.team}:{usage.model}"].append(usage)

        # Evict old records
        if len(self._records) > self._max_records:
            self._records = self._records[-self._max_records // 2:]

    def get_recommendations(self, min_savings_pct: float = 20.0) -> list[Recommendation]:
        """Generate cost optimization recommendations."""
        recommendations = []

        # Analyze each team+model combination
        analyzed = set()
        for key, records in self._by_team_model.items():
            if len(records) < 10:  # Need minimum sample
                continue

            team, model = key.split(":", 1)
            if model in analyzed:
                continue

            # Calculate current cost
            recent = records[-100:]  # Last 100 requests
            avg_input = sum(r.input_tokens for r in recent) / len(recent)
            avg_output = sum(r.output_tokens for r in recent) / len(recent)
            daily_requests = len([r for r in recent if r.timestamp > time.time() - 86400])

            pricing = MODEL_PRICING.get(model)
            if not pricing:
                continue

            current_daily_cost = daily_requests * (
                avg_input / 1_000_000 * pricing["input"] +
                avg_output / 1_000_000 * pricing["output"]
            )

            # Find cheaper alternatives
            for alt_model, alt_pricing in MODEL_PRICING.items():
                if alt_model == model:
                    continue

                alt_daily_cost = daily_requests * (
                    avg_input / 1_000_000 * alt_pricing["input"] +
                    avg_output / 1_000_000 * alt_pricing["output"]
                )

                savings = (current_daily_cost - alt_daily_cost) / max(current_daily_cost, 0.001)
                if savings * 100 < min_savings_pct:
                    continue

                # Estimate quality impact
                quality_impact = self._estimate_quality_impact(model, alt_model)
                if quality_impact == "severe":
                    continue

                recommendations.append(Recommendation(
                    current_model=model,
                    recommended_model=alt_model,
                    team=team,
                    current_cost_per_day=round(current_daily_cost, 2),
                    projected_cost_per_day=round(alt_daily_cost, 2),
                    savings_pct=round(savings * 100, 1),
                    quality_impact=quality_impact,
                    confidence=min(0.95, len(recent) / 100),
                    reason=self._reason(model, alt_model, savings),
                ))

            analyzed.add(model)

        # Sort by savings
        recommendations.sort(key=lambda r: r.savings_pct, reverse=True)
        return recommendations

    def get_spend_summary(self) -> dict:
        """Get current spend breakdown."""
        by_model: dict[str, float] = defaultdict(float)
        by_team: dict[str, float] = defaultdict(float)

        for record in self._records:
            pricing = MODEL_PRICING.get(record.model)
            if not pricing:
                continue
            cost = (
                record.input_tokens / 1_000_000 * pricing["input"] +
                record.output_tokens / 1_000_000 * pricing["output"]
            )
            by_model[record.model] += cost
            by_team[record.team] += cost

        return {
            "total_cost": round(sum(by_model.values()), 2),
            "by_model": {k: round(v, 4) for k, v in sorted(by_model.items(), key=lambda x: -x[1])},
            "by_team": {k: round(v, 4) for k, v in sorted(by_team.items(), key=lambda x: -x[1])},
            "total_records": len(self._records),
        }

    @staticmethod
    def _estimate_quality_impact(current: str, alternative: str) -> str:
        """Heuristic quality impact estimate based on model tier."""
        tiers = {
            "gpt-4o": 4, "claude-sonnet-4-20250514": 4, "claude-3-opus-20240229": 5,
            "gpt-4-turbo": 4, "gemini-1.5-pro": 4,
            "gpt-4o-mini": 3, "claude-3-5-haiku-20241022": 3, "gemini-1.5-flash": 3,
            "gpt-3.5-turbo": 2,
        }
        current_tier = tiers.get(current, 3)
        alt_tier = tiers.get(alternative, 3)
        diff = current_tier - alt_tier

        if diff <= 0:
            return "none"
        elif diff == 1:
            return "minimal"
        elif diff == 2:
            return "moderate"
        else:
            return "severe"

    @staticmethod
    def _reason(current: str, alternative: str, savings: float) -> str:
        return (
            f"Switch from {current} to {alternative} for {savings*100:.0f}% cost reduction. "
            f"Suitable for tasks that don't require the full capability of {current}."
        )
