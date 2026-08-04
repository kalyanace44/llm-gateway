"""Deployment pipeline service — model registry, canary deploys, auto-rollback."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, desc, and_
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.db.deploy_models import ModelVersion, Deployment, DeploymentEvent


class ModelRegistryService:
    """Manages the model registry — versions, promotion, retirement."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def register_model(
        self,
        name: str,
        version: str,
        artifact_uri: str | None = None,
        serving_config: dict | None = None,
        quality_scores: dict | None = None,
        metadata: dict | None = None,
    ) -> ModelVersion:
        """Register a new model version."""
        model = ModelVersion(
            name=name,
            version=version,
            artifact_uri=artifact_uri,
            serving_config=serving_config or {},
            quality_scores=quality_scores or {},
            metadata_=metadata or {},
            status="registered",
        )
        self.session.add(model)
        await self.session.commit()
        await self.session.refresh(model)
        return model

    async def list_models(
        self,
        name: str | None = None,
        status: str | None = None,
    ) -> list[ModelVersion]:
        """List registered models with optional filters."""
        query = select(ModelVersion).order_by(desc(ModelVersion.created_at))
        if name:
            query = query.where(ModelVersion.name == name)
        if status:
            query = query.where(ModelVersion.status == status)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_model(self, model_id: str) -> Optional[ModelVersion]:
        """Get model version by ID."""
        result = await self.session.execute(
            select(ModelVersion).where(ModelVersion.id == model_id)
        )
        return result.scalar_one_or_none()

    async def get_model_by_name_version(self, name: str, version: str) -> Optional[ModelVersion]:
        """Get model by name + version."""
        result = await self.session.execute(
            select(ModelVersion).where(
                ModelVersion.name == name,
                ModelVersion.version == version,
            )
        )
        return result.scalar_one_or_none()

    async def get_production_model(self, name: str) -> Optional[ModelVersion]:
        """Get the current production model for a given name."""
        result = await self.session.execute(
            select(ModelVersion).where(
                ModelVersion.name == name,
                ModelVersion.status == "production",
            )
        )
        return result.scalar_one_or_none()

    async def promote_model(self, model_id: str) -> ModelVersion:
        """Promote a model to production (retire current production version)."""
        model = await self.get_model(model_id)
        if not model:
            raise ValueError(f"Model {model_id} not found")

        # Retire current production version of same name
        current_prod = await self.get_production_model(model.name)
        if current_prod and current_prod.id != model_id:
            current_prod.status = "retired"
            current_prod.retired_at = datetime.now(timezone.utc)

        # Promote new model
        model.status = "production"
        model.promoted_at = datetime.now(timezone.utc)
        await self.session.commit()
        await self.session.refresh(model)
        return model

    async def retire_model(self, model_id: str) -> ModelVersion:
        """Retire a model version."""
        model = await self.get_model(model_id)
        if not model:
            raise ValueError(f"Model {model_id} not found")
        model.status = "retired"
        model.retired_at = datetime.now(timezone.utc)
        await self.session.commit()
        await self.session.refresh(model)
        return model

    async def update_quality_scores(self, model_id: str, scores: dict) -> ModelVersion:
        """Update quality metrics for a model."""
        model = await self.get_model(model_id)
        if not model:
            raise ValueError(f"Model {model_id} not found")
        current = model.quality_scores or {}
        current.update(scores)
        model.quality_scores = current
        await self.session.commit()
        await self.session.refresh(model)
        return model


class DeploymentService:
    """Manages deployment lifecycle — canary, evaluate, promote/rollback."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.registry = ModelRegistryService(session)

    async def create_deployment(
        self,
        model_id: str,
        strategy: str = "canary",
        traffic_pct: float = 0.05,
        config: dict | None = None,
    ) -> Deployment:
        """Create a new deployment."""
        model = await self.registry.get_model(model_id)
        if not model:
            raise ValueError(f"Model {model_id} not found")

        default_config = {
            "bake_time_minutes": 5,
            "rollback_threshold": {"error_rate": 0.1, "latency_p99_multiplier": 2.0},
            "auto_promote": True,
        }
        if config:
            default_config.update(config)

        deploy = Deployment(
            model_id=model_id,
            strategy=strategy,
            traffic_pct=traffic_pct,
            status="pending",
            config=default_config,
        )
        self.session.add(deploy)

        # Update model status
        model.status = "deploying"
        await self.session.commit()
        await self.session.refresh(deploy)

        # Log event
        await self._log_event(deploy.id, "state_change", {
            "from": "pending", "to": "deploying",
            "model": model.name, "version": model.version,
        })

        return deploy

    async def advance_deployment(self, deployment_id: str, metrics: dict | None = None) -> Deployment:
        """Advance deployment through stages: pending → deploying → baking → evaluating → promoted/rolled_back."""
        deploy = await self.get_deployment(deployment_id)
        if not deploy:
            raise ValueError(f"Deployment {deployment_id} not found")

        old_status = deploy.status

        if deploy.status == "pending":
            deploy.status = "deploying"
        elif deploy.status == "deploying":
            deploy.status = "baking"
        elif deploy.status == "baking":
            deploy.status = "evaluating"
        elif deploy.status == "evaluating":
            # Check metrics against thresholds
            should_promote = self._evaluate_metrics(deploy, metrics)
            if should_promote:
                deploy.status = "promoted"
                deploy.completed_at = datetime.now(timezone.utc)
                # Promote the model
                await self.registry.promote_model(deploy.model_id)
            else:
                deploy.status = "rolled_back"
                deploy.completed_at = datetime.now(timezone.utc)
                # Revert model status
                model = await self.registry.get_model(deploy.model_id)
                if model:
                    model.status = "registered"

        if metrics:
            current_metrics = deploy.metrics or {}
            current_metrics.update(metrics)
            deploy.metrics = current_metrics

        await self.session.commit()
        await self.session.refresh(deploy)

        await self._log_event(deploy.id, "state_change", {
            "from": old_status, "to": deploy.status,
            "metrics": metrics,
        })

        return deploy

    async def rollback_deployment(self, deployment_id: str, reason: str = "") -> Deployment:
        """Force rollback a deployment."""
        deploy = await self.get_deployment(deployment_id)
        if not deploy:
            raise ValueError(f"Deployment {deployment_id} not found")

        deploy.status = "rolled_back"
        deploy.completed_at = datetime.now(timezone.utc)
        deploy.error_message = reason or "Manual rollback"

        # Revert model status
        model = await self.registry.get_model(deploy.model_id)
        if model:
            model.status = "registered"

        await self.session.commit()
        await self.session.refresh(deploy)

        await self._log_event(deploy.id, "rollback_triggered", {
            "reason": reason, "model_id": deploy.model_id,
        })

        return deploy

    async def get_deployment(self, deployment_id: str) -> Optional[Deployment]:
        """Get deployment by ID."""
        result = await self.session.execute(
            select(Deployment).where(Deployment.id == deployment_id)
        )
        return result.scalar_one_or_none()

    async def list_deployments(
        self,
        status: str | None = None,
        limit: int = 20,
    ) -> list[Deployment]:
        """List deployments."""
        query = select(Deployment).order_by(desc(Deployment.started_at)).limit(limit)
        if status:
            query = query.where(Deployment.status == status)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_deployment_events(self, deployment_id: str) -> list[DeploymentEvent]:
        """Get events for a deployment."""
        result = await self.session.execute(
            select(DeploymentEvent)
            .where(DeploymentEvent.deployment_id == deployment_id)
            .order_by(DeploymentEvent.created_at)
        )
        return list(result.scalars().all())

    def _evaluate_metrics(self, deploy: Deployment, metrics: dict | None) -> bool:
        """Evaluate if deployment metrics pass thresholds."""
        if not metrics:
            # No metrics = auto-promote (trust the pipeline)
            config = deploy.config or {}
            return config.get("auto_promote", True)

        config = deploy.config or {}
        thresholds = config.get("rollback_threshold", {})

        # Check error rate
        error_rate = metrics.get("error_rate", 0)
        max_error_rate = thresholds.get("error_rate", 0.1)
        if error_rate > max_error_rate:
            return False

        # Check latency
        latency_p99 = metrics.get("latency_p99", 0)
        baseline_p99 = metrics.get("baseline_latency_p99", latency_p99)
        multiplier = thresholds.get("latency_p99_multiplier", 2.0)
        if baseline_p99 > 0 and latency_p99 > baseline_p99 * multiplier:
            return False

        return True

    async def _log_event(self, deployment_id: str, event_type: str, detail: dict):
        """Log a deployment event."""
        event = DeploymentEvent(
            deployment_id=deployment_id,
            event_type=event_type,
            detail=detail,
        )
        self.session.add(event)
        await self.session.commit()
