"""Experiment tracking service — A/B tests, variant assignment, statistical analysis."""
from __future__ import annotations

import math
import random
import time
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, desc, func
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.db.experiment_models import Experiment, ExperimentEvent


class ExperimentService:
    """Manages experiments: creation, variant assignment, event recording, analysis."""

    def __init__(self, session: AsyncSession):
        self.session = session

    # --- CRUD ---

    async def create_experiment(
        self,
        name: str,
        config: dict,
        description: str | None = None,
        tenant_ids: list[str] | None = None,
    ) -> Experiment:
        """Create a new experiment."""
        experiment = Experiment(
            name=name,
            description=description,
            tenant_ids=tenant_ids or [],
            config=config,
            status="draft",
        )
        self.session.add(experiment)
        await self.session.commit()
        await self.session.refresh(experiment)
        return experiment

    async def get_experiment(self, experiment_id: str) -> Optional[Experiment]:
        """Get experiment by ID."""
        result = await self.session.execute(
            select(Experiment).where(Experiment.id == experiment_id)
        )
        return result.scalar_one_or_none()

    async def list_experiments(
        self, status: str | None = None, limit: int = 20
    ) -> list[Experiment]:
        """List experiments."""
        query = select(Experiment).order_by(desc(Experiment.created_at)).limit(limit)
        if status:
            query = query.where(Experiment.status == status)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    # --- Lifecycle ---

    async def start_experiment(self, experiment_id: str) -> Experiment:
        """Start an experiment (draft → running)."""
        exp = await self.get_experiment(experiment_id)
        if not exp:
            raise ValueError(f"Experiment {experiment_id} not found")
        if exp.status != "draft":
            raise ValueError(f"Can only start draft experiments, current: {exp.status}")
        exp.status = "running"
        exp.started_at = datetime.now(timezone.utc)
        await self.session.commit()
        await self.session.refresh(exp)
        return exp

    async def stop_experiment(self, experiment_id: str) -> Experiment:
        """Stop an experiment (running → completed)."""
        exp = await self.get_experiment(experiment_id)
        if not exp:
            raise ValueError(f"Experiment {experiment_id} not found")
        if exp.status != "running":
            raise ValueError(f"Can only stop running experiments, current: {exp.status}")
        exp.status = "completed"
        exp.ended_at = datetime.now(timezone.utc)

        # Compute final results
        results = await self._compute_results(experiment_id)
        exp.results = results

        await self.session.commit()
        await self.session.refresh(exp)
        return exp

    async def cancel_experiment(self, experiment_id: str) -> Experiment:
        """Cancel an experiment."""
        exp = await self.get_experiment(experiment_id)
        if not exp:
            raise ValueError(f"Experiment {experiment_id} not found")
        exp.status = "cancelled"
        exp.ended_at = datetime.now(timezone.utc)
        await self.session.commit()
        await self.session.refresh(exp)
        return exp

    # --- Variant Assignment ---

    def assign_variant(self, experiment: Experiment, tenant_id: str | None = None) -> str | None:
        """Assign a variant for a request based on experiment config.
        Returns variant name or None if experiment doesn't apply.
        """
        if experiment.status != "running":
            return None

        # Check tenant eligibility
        tenant_ids = experiment.tenant_ids or []
        if tenant_ids and tenant_id and tenant_id not in tenant_ids:
            return None

        # Weighted random assignment
        config = experiment.config or {}
        variants = config.get("variants", [])
        if not variants:
            return None

        weights = [v.get("weight", 1.0) for v in variants]
        total = sum(weights)
        normalized = [w / total for w in weights]

        r = random.random()
        cumulative = 0.0
        for variant, weight in zip(variants, normalized):
            cumulative += weight
            if r <= cumulative:
                return variant["name"]

        return variants[-1]["name"]

    async def get_active_experiments(self, tenant_id: str | None = None) -> list[Experiment]:
        """Get running experiments applicable to a tenant."""
        result = await self.session.execute(
            select(Experiment).where(Experiment.status == "running")
        )
        experiments = list(result.scalars().all())

        # Filter by tenant eligibility
        if tenant_id:
            applicable = []
            for exp in experiments:
                tenant_ids = exp.tenant_ids or []
                if not tenant_ids or tenant_id in tenant_ids:
                    applicable.append(exp)
            return applicable
        return experiments

    # --- Event Recording ---

    async def record_event(
        self,
        experiment_id: str,
        variant: str,
        metrics: dict,
        tenant_id: str | None = None,
        request_id: str | None = None,
    ) -> ExperimentEvent:
        """Record an observation for an experiment variant."""
        event = ExperimentEvent(
            experiment_id=experiment_id,
            variant=variant,
            tenant_id=tenant_id,
            request_id=request_id,
            metrics=metrics,
        )
        self.session.add(event)

        # Update sample count
        exp = await self.get_experiment(experiment_id)
        if exp:
            exp.sample_count = (exp.sample_count or 0) + 1

        await self.session.commit()
        await self.session.refresh(event)
        return event

    # --- Statistical Analysis ---

    async def get_results(self, experiment_id: str) -> dict:
        """Compute experiment results with statistical analysis."""
        return await self._compute_results(experiment_id)

    async def _compute_results(self, experiment_id: str) -> dict:
        """Compute per-variant statistics and comparison."""
        exp = await self.get_experiment(experiment_id)
        if not exp:
            return {}

        config = exp.config or {}
        variants = config.get("variants", [])
        tracked_metrics = config.get("metrics", ["latency", "cost"])

        # Fetch all events
        result = await self.session.execute(
            select(ExperimentEvent)
            .where(ExperimentEvent.experiment_id == experiment_id)
            .order_by(ExperimentEvent.created_at)
        )
        events = list(result.scalars().all())

        if not events:
            return {"status": "no_data", "total_events": 0}

        # Group by variant
        variant_events: dict[str, list[dict]] = {}
        for event in events:
            variant_name = event.variant
            if variant_name not in variant_events:
                variant_events[variant_name] = []
            variant_events[variant_name].append(event.metrics or {})

        # Compute per-variant stats
        variant_stats = {}
        for variant_name, metrics_list in variant_events.items():
            stats = {"sample_size": len(metrics_list)}
            for metric in tracked_metrics:
                values = [m.get(metric) for m in metrics_list if m.get(metric) is not None]
                if values:
                    stats[metric] = {
                        "mean": round(sum(values) / len(values), 4),
                        "std": round(self._std(values), 4),
                        "min": round(min(values), 4),
                        "max": round(max(values), 4),
                        "p50": round(self._percentile(values, 0.5), 4),
                        "p95": round(self._percentile(values, 0.95), 4),
                        "count": len(values),
                    }
            variant_stats[variant_name] = stats

        # Comparison (if 2 variants, compute confidence)
        comparison = {}
        variant_names = list(variant_stats.keys())
        if len(variant_names) == 2:
            control = variant_names[0]
            treatment = variant_names[1]
            for metric in tracked_metrics:
                if metric in variant_stats.get(control, {}) and metric in variant_stats.get(treatment, {}):
                    c_stats = variant_stats[control][metric]
                    t_stats = variant_stats[treatment][metric]
                    # Compute relative lift
                    if c_stats["mean"] != 0:
                        lift = (t_stats["mean"] - c_stats["mean"]) / c_stats["mean"]
                    else:
                        lift = 0
                    # Z-score for significance
                    z_score, p_value = self._z_test(
                        c_stats["mean"], c_stats["std"], c_stats["count"],
                        t_stats["mean"], t_stats["std"], t_stats["count"],
                    )
                    comparison[metric] = {
                        "control_mean": c_stats["mean"],
                        "treatment_mean": t_stats["mean"],
                        "lift_pct": round(lift * 100, 2),
                        "z_score": round(z_score, 3),
                        "p_value": round(p_value, 4),
                        "significant": p_value < 0.05,
                    }

        return {
            "total_events": len(events),
            "variants": variant_stats,
            "comparison": comparison,
            "status": "complete" if exp.status == "completed" else "in_progress",
        }

    @staticmethod
    def _std(values: list[float]) -> float:
        """Standard deviation."""
        if len(values) < 2:
            return 0.0
        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
        return math.sqrt(variance)

    @staticmethod
    def _percentile(values: list[float], pct: float) -> float:
        """Compute percentile."""
        sorted_vals = sorted(values)
        idx = int(len(sorted_vals) * pct)
        return sorted_vals[min(idx, len(sorted_vals) - 1)]

    @staticmethod
    def _z_test(
        mean1: float, std1: float, n1: int,
        mean2: float, std2: float, n2: int,
    ) -> tuple[float, float]:
        """Two-sample z-test for means."""
        if n1 == 0 or n2 == 0:
            return 0.0, 1.0

        se = math.sqrt((std1 ** 2 / max(n1, 1)) + (std2 ** 2 / max(n2, 1)))
        if se == 0:
            return 0.0, 1.0

        z = (mean2 - mean1) / se

        # Approximate p-value using normal CDF
        p_value = 2 * (1 - ExperimentService._normal_cdf(abs(z)))
        return z, p_value

    @staticmethod
    def _normal_cdf(x: float) -> float:
        """Approximate standard normal CDF."""
        return 0.5 * (1 + math.erf(x / math.sqrt(2)))
