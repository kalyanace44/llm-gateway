"""SQLAlchemy database models for persistent memory and multi-tenancy."""
from __future__ import annotations

import uuid
from datetime import datetime

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
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    """Base model class."""
    pass


class Tenant(Base):
    """Multi-tenant organization."""

    __tablename__ = "tenants"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(255), nullable=False)
    slug = Column(String(64), unique=True, nullable=False)
    tier = Column(String(20), default="free")  # free, pro, enterprise
    config = Column(JSON, default=dict)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    conversations = relationship("Conversation", back_populates="tenant", lazy="selectin")
    api_keys = relationship("TenantAPIKey", back_populates="tenant", lazy="selectin")


class TenantAPIKey(Base):
    """API keys belonging to a tenant."""

    __tablename__ = "tenant_api_keys"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String(36), ForeignKey("tenants.id"), nullable=False)
    key_hash = Column(String(64), unique=True, nullable=False)
    name = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True)
    rate_limit_rpm = Column(Integer, nullable=True)
    rate_limit_tpm = Column(Integer, nullable=True)
    allowed_models = Column(JSON, default=list)
    budget_usd = Column(Float, nullable=True)
    spent_usd = Column(Float, default=0.0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_used_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    tenant = relationship("Tenant", back_populates="api_keys")


class Conversation(Base):
    """A conversation session belonging to a tenant."""

    __tablename__ = "conversations"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String(36), ForeignKey("tenants.id"), nullable=False)
    session_id = Column(String(255), nullable=False)
    title = Column(String(512), nullable=True)
    metadata_ = Column("metadata", JSON, default=dict)
    message_count = Column(Integer, default=0)
    total_tokens = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    tenant = relationship("Tenant", back_populates="conversations")
    messages = relationship("Message", back_populates="conversation", lazy="selectin",
                           order_by="Message.created_at")
    summaries = relationship("MemorySummary", back_populates="conversation", lazy="selectin")

    __table_args__ = (
        Index("idx_conv_tenant_session", "tenant_id", "session_id"),
    )


class Message(Base):
    """A single message in a conversation."""

    __tablename__ = "messages"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id = Column(String(36), ForeignKey("conversations.id"), nullable=False)
    role = Column(String(20), nullable=False)  # user, assistant, system, tool
    content = Column(Text, nullable=False)
    token_count = Column(Integer, default=0)
    model = Column(String(255), nullable=True)
    cost_usd = Column(Float, default=0.0)
    metadata_ = Column("metadata", JSON, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    conversation = relationship("Conversation", back_populates="messages")

    __table_args__ = (
        Index("idx_msg_conv_created", "conversation_id", "created_at"),
    )


class MemorySummary(Base):
    """Distilled summary of a conversation segment."""

    __tablename__ = "memory_summaries"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String(36), ForeignKey("tenants.id"), nullable=False)
    conversation_id = Column(String(36), ForeignKey("conversations.id"), nullable=False)
    summary = Column(Text, nullable=False)
    message_start_idx = Column(Integer, nullable=False)
    message_end_idx = Column(Integer, nullable=False)
    token_count = Column(Integer, default=0)
    metadata_ = Column("metadata", JSON, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    conversation = relationship("Conversation", back_populates="summaries")

    __table_args__ = (
        Index("idx_summary_tenant", "tenant_id"),
    )
