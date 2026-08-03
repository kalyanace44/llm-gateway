"""RAG API — knowledge bases, document ingestion, and retrieval."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.db.session import get_session
from gateway.services.rag import RAGService, EmbeddingService
from gateway.api.memory import _resolve_tenant

router = APIRouter(prefix="/v1/rag", tags=["rag"])


# --- Request/Response models ---

class CreateKBRequest(BaseModel):
    name: str
    description: str | None = None
    config: dict | None = None


class IngestDocumentRequest(BaseModel):
    content: str = Field(..., min_length=1)
    filename: str | None = None
    source_url: str | None = None
    metadata: dict | None = None


class RetrieveRequest(BaseModel):
    query: str = Field(..., min_length=1)
    knowledge_base_ids: list[str] | None = None
    top_k: int = Field(5, ge=1, le=50)
    score_threshold: float = Field(0.0, ge=0.0, le=1.0)
    include_sources: bool = True


# --- Helpers ---

def _get_embedding_svc() -> EmbeddingService:
    """Create embedding service from config."""
    from gateway.config import config, settings
    # Use first backend as embedding provider, or localhost default
    base_url = "http://localhost:8000/v1"
    api_key = ""
    for backend in config.backends:
        if backend.enabled:
            base_url = backend.base_url
            api_key = backend.api_key
            break
    return EmbeddingService(base_url=base_url, api_key=api_key)


# --- Endpoints ---

@router.post("/knowledge-bases")
async def create_knowledge_base(
    body: CreateKBRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Create a new knowledge base."""
    tenant = await _resolve_tenant(request, session)
    embedding_svc = _get_embedding_svc()
    rag = RAGService(session, embedding_svc)

    kb = await rag.create_knowledge_base(
        tenant_id=tenant.id,
        name=body.name,
        description=body.description,
        config=body.config,
    )
    await embedding_svc.close()
    return {
        "id": kb.id,
        "tenant_id": kb.tenant_id,
        "name": kb.name,
        "description": kb.description,
        "status": kb.status,
        "created_at": kb.created_at.isoformat() if kb.created_at else None,
    }


@router.get("/knowledge-bases")
async def list_knowledge_bases(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """List knowledge bases for a tenant."""
    tenant = await _resolve_tenant(request, session)
    embedding_svc = _get_embedding_svc()
    rag = RAGService(session, embedding_svc)

    kbs = await rag.list_knowledge_bases(tenant.id)
    await embedding_svc.close()
    return {
        "knowledge_bases": [
            {
                "id": kb.id,
                "name": kb.name,
                "description": kb.description,
                "status": kb.status,
                "document_count": kb.document_count,
                "chunk_count": kb.chunk_count,
                "created_at": kb.created_at.isoformat() if kb.created_at else None,
            }
            for kb in kbs
        ]
    }


@router.get("/knowledge-bases/{kb_id}")
async def get_knowledge_base(
    kb_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Get knowledge base details."""
    tenant = await _resolve_tenant(request, session)
    embedding_svc = _get_embedding_svc()
    rag = RAGService(session, embedding_svc)

    kb = await rag.get_knowledge_base(kb_id, tenant.id)
    await embedding_svc.close()
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    return {
        "id": kb.id,
        "name": kb.name,
        "description": kb.description,
        "config": kb.config,
        "status": kb.status,
        "document_count": kb.document_count,
        "chunk_count": kb.chunk_count,
        "created_at": kb.created_at.isoformat() if kb.created_at else None,
    }


@router.post("/knowledge-bases/{kb_id}/documents")
async def ingest_document(
    kb_id: str,
    body: IngestDocumentRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Ingest a document into a knowledge base (chunk + embed + index)."""
    tenant = await _resolve_tenant(request, session)
    embedding_svc = _get_embedding_svc()
    rag = RAGService(session, embedding_svc)

    # Verify KB belongs to tenant
    kb = await rag.get_knowledge_base(kb_id, tenant.id)
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    try:
        doc = await rag.ingest_document(
            kb_id=kb_id,
            content=body.content,
            filename=body.filename,
            source_url=body.source_url,
            metadata=body.metadata,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {str(e)}")
    finally:
        await embedding_svc.close()

    return {
        "id": doc.id,
        "kb_id": doc.kb_id,
        "filename": doc.filename,
        "status": doc.status,
        "chunk_count": doc.chunk_count,
        "total_tokens": doc.total_tokens,
        "processed_at": doc.processed_at.isoformat() if doc.processed_at else None,
    }


@router.get("/knowledge-bases/{kb_id}/status")
async def get_kb_status(
    kb_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Get ingestion status for a knowledge base."""
    tenant = await _resolve_tenant(request, session)
    embedding_svc = _get_embedding_svc()
    rag = RAGService(session, embedding_svc)

    kb = await rag.get_knowledge_base(kb_id, tenant.id)
    await embedding_svc.close()
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    return {
        "id": kb.id,
        "name": kb.name,
        "status": kb.status,
        "document_count": kb.document_count,
        "chunk_count": kb.chunk_count,
    }


@router.post("/retrieve")
async def retrieve(
    body: RetrieveRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Retrieve relevant chunks from knowledge bases (hybrid search)."""
    tenant = await _resolve_tenant(request, session)
    embedding_svc = _get_embedding_svc()
    rag = RAGService(session, embedding_svc)

    results = await rag.retrieve(
        tenant_id=tenant.id,
        query=body.query,
        kb_ids=body.knowledge_base_ids,
        top_k=body.top_k,
        score_threshold=body.score_threshold,
    )
    await embedding_svc.close()

    return {
        "query": body.query,
        "results": [
            {
                "chunk_id": r["chunk_id"],
                "document_id": r["document_id"],
                "content": r["content"],
                "score": r["score"],
                "source": r["source"],
                "metadata": r.get("metadata", {}),
            }
            for r in results
        ],
        "total": len(results),
    }


@router.delete("/knowledge-bases/{kb_id}")
async def delete_knowledge_base(
    kb_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Delete a knowledge base."""
    tenant = await _resolve_tenant(request, session)
    embedding_svc = _get_embedding_svc()
    rag = RAGService(session, embedding_svc)

    deleted = await rag.delete_knowledge_base(kb_id, tenant.id)
    await embedding_svc.close()
    if not deleted:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    return {"status": "deleted", "id": kb_id}
