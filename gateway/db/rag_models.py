"""Database models for RAG — knowledge bases, documents, chunks."""
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
    LargeBinary,
    func,
)
from sqlalchemy.orm import relationship

from gateway.db.models import Base


class KnowledgeBase(Base):
    """A knowledge base belonging to a tenant."""

    __tablename__ = "knowledge_bases"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String(36), ForeignKey("tenants.id"), nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    config = Column(JSON, default=dict)  # chunk_size, overlap, embedding_model
    status = Column(String(20), default="active")  # active, processing, disabled
    document_count = Column(Integer, default=0)
    chunk_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    documents = relationship("Document", back_populates="knowledge_base", lazy="selectin")

    __table_args__ = (
        Index("idx_kb_tenant", "tenant_id"),
    )


class Document(Base):
    """A document uploaded to a knowledge base."""

    __tablename__ = "documents"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    kb_id = Column(String(36), ForeignKey("knowledge_bases.id"), nullable=False)
    filename = Column(String(512), nullable=True)
    source_url = Column(Text, nullable=True)
    content_hash = Column(String(64), nullable=True)
    doc_metadata = Column(JSON, default=dict)
    status = Column(String(20), default="processing")  # processing, ready, failed
    chunk_count = Column(Integer, default=0)
    total_tokens = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    processed_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    knowledge_base = relationship("KnowledgeBase", back_populates="documents")
    chunks = relationship("Chunk", back_populates="document", lazy="selectin")

    __table_args__ = (
        Index("idx_doc_kb", "kb_id"),
    )


class Chunk(Base):
    """A text chunk with embedding for retrieval."""

    __tablename__ = "chunks"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id = Column(String(36), ForeignKey("documents.id"), nullable=False)
    kb_id = Column(String(36), ForeignKey("knowledge_bases.id"), nullable=False)
    content = Column(Text, nullable=False)
    embedding = Column(LargeBinary, nullable=True)  # numpy bytes; pgvector in prod
    chunk_index = Column(Integer, nullable=False)
    token_count = Column(Integer, default=0)
    metadata_ = Column("metadata", JSON, default=dict)  # page, section, heading
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    document = relationship("Document", back_populates="chunks")

    __table_args__ = (
        Index("idx_chunk_kb", "kb_id"),
        Index("idx_chunk_doc", "document_id"),
    )
