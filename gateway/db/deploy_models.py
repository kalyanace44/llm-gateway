"""Database models for deployment pipeline and model registry."""
from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    Float,
    Index,
    JSON,
    func,
)
from sqlalchemy.orm import relationship

from gateway.db.models import Base


class ModelVersion(Base):
    """A registered model version in the registry."""

    __tablename__ = "model_registry"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(255), nullable=False)           # e.g. "llama-3.1-70b"
    version = Column(String(64), nullable=False)          # e.g. "v2-finetuned-2024-01"
    artifact_uri = Column(Text, nullable=True)            # s3://models/llama-3.1-70b/v2/
    serving_config = Column(JSON, default=dict)           # tensor_parallel, quantization, max_batch
    status = Column(String(20), default="registered")     # registered, deploying, canary, production, retired
    quality_scores = Column(JSON, default=dict)           # bertscore, bleu, latency_p50, cost_per_1k
    metadata_ = Column("metadata", JSON, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    promoted_at = Column(DateTime(timezone=True), nullable=True)
    retired_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    deployments = relationship("Deployment", back_populates="model_version", lazy="selectin")

    __table_args__ = (
        Index("idx_model_name_version", "name", "version", unique=True),
        Index("idx_model_status", "status"),
    )


class Deployment(Base):
    """A deployment of a model version (canary, blue-green, shadow)."""

    __tablename__ = "deployments"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    model_id = Column(String(36), ForeignKey("model_registry.id"), nullable=False)
    strategy = Column(String(20), nullable=False)         # canary, blue-green, shadow
    traffic_pct = Column(Float, default=0.05)             # % of traffic to new version
    status = Column(String(20), default="pending")        # pending, deploying, baking, evaluating, promoted, rolled_back, failed
    config = Column(JSON, default=dict)                   # bake_time_minutes, rollback_threshold, eval_suite
    metrics = Column(JSON, default=dict)                  # collected during deployment
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    model_version = relationship("ModelVersion", back_populates="deployments")

    __table_args__ = (
        Index("idx_deploy_status", "status"),
        Index("idx_deploy_model", "model_id"),
    )


class DeploymentEvent(Base):
    """Events during a deployment (state transitions, metrics snapshots)."""

    __tablename__ = "deployment_events"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    deployment_id = Column(String(36), ForeignKey("deployments.id"), nullable=False)
    event_type = Column(String(50), nullable=False)       # state_change, metric_snapshot, rollback_triggered, promoted
    detail = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_event_deploy", "deployment_id"),
    )
