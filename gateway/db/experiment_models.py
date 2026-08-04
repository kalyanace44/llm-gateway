"""Database models for experiment tracking."""
from __future__ import annotations

import uuid

from sqlalchemy import (
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


class Experiment(Base):
    """An A/B or multivariate experiment."""

    __tablename__ = "experiments"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    tenant_ids = Column(JSON, default=list)             # which tenants participate (empty = all)
    config = Column(JSON, nullable=False)
    # config example:
    # {
    #   "variants": [
    #     {"name": "control", "model": "llama-3.1-70b-v1", "weight": 0.5},
    #     {"name": "treatment", "model": "llama-3.1-70b-v2-ft", "weight": 0.5}
    #   ],
    #   "metrics": ["latency", "user_rating", "cost"],
    #   "duration_hours": 168,
    #   "min_samples": 1000
    # }
    status = Column(String(20), default="draft")        # draft, running, completed, cancelled
    results = Column(JSON, default=dict)                # statistical analysis results
    sample_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    started_at = Column(DateTime(timezone=True), nullable=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    events = relationship("ExperimentEvent", back_populates="experiment", lazy="selectin")

    __table_args__ = (
        Index("idx_experiment_status", "status"),
    )


class ExperimentEvent(Base):
    """A single observation in an experiment (one request's metrics)."""

    __tablename__ = "experiment_events"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    experiment_id = Column(String(36), ForeignKey("experiments.id"), nullable=False)
    variant = Column(String(64), nullable=False)
    tenant_id = Column(String(36), nullable=True)
    request_id = Column(String(36), nullable=True)
    metrics = Column(JSON, nullable=False)              # {latency: 1.2, tokens: 450, cost: 0.003}
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    experiment = relationship("Experiment", back_populates="events")

    __table_args__ = (
        Index("idx_event_experiment", "experiment_id"),
        Index("idx_event_variant", "experiment_id", "variant"),
    )
