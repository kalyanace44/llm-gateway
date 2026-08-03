"""Persistent memory API — conversations and recall."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.db.session import get_session
from gateway.services.memory import MemoryService
from gateway.services.tenant import TenantService

router = APIRouter(prefix="/v1/memory", tags=["memory"])


# --- Request/Response models ---

class CreateConversationRequest(BaseModel):
    session_id: str
    title: str | None = None
    metadata: dict | None = None


class AddMessageRequest(BaseModel):
    role: str = Field(..., pattern=r"^(user|assistant|system|tool)$")
    content: str
    token_count: int = 0
    model: str | None = None
    cost_usd: float = 0.0
    metadata: dict | None = None


# --- Tenant resolution helper ---

async def _resolve_tenant(request: Request, session: AsyncSession):
    """Resolve tenant from X-Tenant-ID header or API key."""
    tenant_id = request.headers.get("X-Tenant-ID")
    if not tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header required")
    svc = TenantService(session)
    # Try as direct ID first, then as slug
    tenant = await svc.get_tenant(tenant_id)
    if not tenant:
        tenant = await svc.get_tenant_by_slug(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant


# --- Endpoints ---

@router.post("/conversations")
async def create_conversation(
    body: CreateConversationRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Start a new conversation."""
    tenant = await _resolve_tenant(request, session)
    mem = MemoryService(session)
    conv = await mem.create_conversation(
        tenant_id=tenant.id,
        session_id=body.session_id,
        title=body.title,
        metadata=body.metadata,
    )
    return {
        "id": str(conv.id),
        "tenant_id": str(conv.tenant_id),
        "session_id": conv.session_id,
        "title": conv.title,
        "created_at": conv.created_at.isoformat() if conv.created_at else None,
    }


@router.get("/conversations")
async def list_conversations(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    """List conversations for a tenant."""
    tenant = await _resolve_tenant(request, session)
    mem = MemoryService(session)
    convs = await mem.list_conversations(tenant.id, limit=limit, offset=offset)
    return {
        "conversations": [
            {
                "id": str(c.id),
                "session_id": c.session_id,
                "title": c.title,
                "message_count": c.message_count,
                "total_tokens": c.total_tokens,
                "updated_at": c.updated_at.isoformat() if c.updated_at else None,
            }
            for c in convs
        ]
    }


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
):
    """Get a conversation with messages."""
    tenant = await _resolve_tenant(request, session)
    mem = MemoryService(session)
    conv = await mem.get_conversation(conversation_id, tenant.id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    messages = await mem.get_messages(conversation_id, limit=limit)
    return {
        "id": str(conv.id),
        "session_id": conv.session_id,
        "title": conv.title,
        "message_count": conv.message_count,
        "messages": [
            {
                "id": str(m.id),
                "role": m.role,
                "content": m.content,
                "token_count": m.token_count,
                "model": m.model,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in messages
        ],
    }


@router.post("/conversations/{conversation_id}/messages")
async def add_message(
    conversation_id: str,
    body: AddMessageRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Add a message to a conversation."""
    tenant = await _resolve_tenant(request, session)
    mem = MemoryService(session)

    # Verify conversation belongs to tenant
    conv = await mem.get_conversation(conversation_id, tenant.id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    msg = await mem.add_message(
        conversation_id=conversation_id,
        role=body.role,
        content=body.content,
        token_count=body.token_count,
        model=body.model,
        cost_usd=body.cost_usd,
        metadata=body.metadata,
    )
    return {
        "id": str(msg.id),
        "conversation_id": str(msg.conversation_id),
        "role": msg.role,
        "token_count": msg.token_count,
        "created_at": msg.created_at.isoformat() if msg.created_at else None,
    }


@router.get("/recall")
async def recall_memory(
    request: Request,
    query: str = Query(..., min_length=1),
    limit: int = Query(5, ge=1, le=20),
    session: AsyncSession = Depends(get_session),
):
    """Recall relevant messages across conversations (semantic search)."""
    tenant = await _resolve_tenant(request, session)
    mem = MemoryService(session)
    messages = await mem.recall(tenant.id, query=query, limit=limit)
    return {
        "results": [
            {
                "id": str(m.id),
                "conversation_id": str(m.conversation_id),
                "role": m.role,
                "content": m.content[:500],  # Truncate for recall results
                "token_count": m.token_count,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in messages
        ]
    }


@router.get("/context/{conversation_id}")
async def get_context_window(
    conversation_id: str,
    request: Request,
    max_tokens: int = Query(4000, ge=100, le=128000),
    session: AsyncSession = Depends(get_session),
):
    """Get recent messages fitting within a token budget (for injection)."""
    tenant = await _resolve_tenant(request, session)
    mem = MemoryService(session)

    conv = await mem.get_conversation(conversation_id, tenant.id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    messages = await mem.get_context_window(conversation_id, max_tokens=max_tokens)
    return {
        "conversation_id": str(conversation_id),
        "messages": [
            {"role": m.role, "content": m.content}
            for m in messages
        ],
        "total_tokens": sum(m.token_count for m in messages),
    }
