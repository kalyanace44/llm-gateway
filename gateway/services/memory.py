"""Persistent memory service — conversation storage and recall."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.db.models import Conversation, Message, MemorySummary


class MemoryService:
    """Manages persistent conversation memory per tenant."""

    def __init__(self, session: AsyncSession):
        self.session = session

    # --- Conversations ---

    async def create_conversation(
        self,
        tenant_id: str,
        session_id: str,
        title: str | None = None,
        metadata: dict | None = None,
    ) -> Conversation:
        """Start a new conversation."""
        conv = Conversation(
            tenant_id=tenant_id,
            session_id=session_id,
            title=title,
            metadata_=metadata or {},
        )
        self.session.add(conv)
        await self.session.commit()
        await self.session.refresh(conv)
        return conv

    async def get_conversation(
        self, conversation_id: str, tenant_id: str
    ) -> Optional[Conversation]:
        """Get a conversation (tenant-scoped)."""
        result = await self.session.execute(
            select(Conversation).where(
                Conversation.id == str(conversation_id),
                Conversation.tenant_id == str(tenant_id),
            )
        )
        return result.scalar_one_or_none()

    async def get_or_create_conversation(
        self, tenant_id: str, session_id: str
    ) -> Conversation:
        """Get existing conversation by session_id or create new one."""
        result = await self.session.execute(
            select(Conversation).where(
                Conversation.tenant_id == tenant_id,
                Conversation.session_id == session_id,
                Conversation.is_active == True,
            )
        )
        conv = result.scalar_one_or_none()
        if conv:
            return conv
        return await self.create_conversation(tenant_id, session_id)

    async def list_conversations(
        self,
        tenant_id: str,
        limit: int = 20,
        offset: int = 0,
        active_only: bool = True,
    ) -> list[Conversation]:
        """List conversations for a tenant."""
        query = (
            select(Conversation)
            .where(Conversation.tenant_id == tenant_id)
            .order_by(desc(Conversation.updated_at))
            .limit(limit)
            .offset(offset)
        )
        if active_only:
            query = query.where(Conversation.is_active == True)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    # --- Messages ---

    async def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        token_count: int = 0,
        model: str | None = None,
        cost_usd: float = 0.0,
        metadata: dict | None = None,
    ) -> Message:
        """Add a message to a conversation."""
        msg = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            token_count=token_count,
            model=model,
            cost_usd=cost_usd,
            metadata_=metadata or {},
        )
        self.session.add(msg)

        # Update conversation stats
        result = await self.session.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        conv = result.scalar_one_or_none()
        if conv:
            conv.message_count = (conv.message_count or 0) + 1
            conv.total_tokens = (conv.total_tokens or 0) + token_count
            conv.updated_at = datetime.now(timezone.utc)

        await self.session.commit()
        await self.session.refresh(msg)
        return msg

    async def get_messages(
        self,
        conversation_id: str,
        limit: int = 50,
        before: datetime | None = None,
    ) -> list[Message]:
        """Get messages from a conversation (most recent first)."""
        query = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(desc(Message.created_at))
            .limit(limit)
        )
        if before:
            query = query.where(Message.created_at < before)
        result = await self.session.execute(query)
        messages = list(result.scalars().all())
        messages.reverse()  # Return chronological order
        return messages

    async def get_context_window(
        self,
        conversation_id: str,
        max_tokens: int = 4000,
    ) -> list[Message]:
        """Get recent messages fitting within a token budget."""
        messages = await self.get_messages(conversation_id, limit=100)
        # Walk backwards, accumulating tokens
        context = []
        token_sum = 0
        for msg in reversed(messages):
            if token_sum + msg.token_count > max_tokens:
                break
            context.insert(0, msg)
            token_sum += msg.token_count
        return context

    # --- Summaries ---

    async def add_summary(
        self,
        tenant_id: str,
        conversation_id: str,
        summary: str,
        message_start_idx: int,
        message_end_idx: int,
        token_count: int = 0,
    ) -> MemorySummary:
        """Store a conversation summary."""
        mem = MemorySummary(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            summary=summary,
            message_start_idx=message_start_idx,
            message_end_idx=message_end_idx,
            token_count=token_count,
        )
        self.session.add(mem)
        await self.session.commit()
        await self.session.refresh(mem)
        return mem

    async def get_summaries(
        self,
        tenant_id: str,
        limit: int = 10,
    ) -> list[MemorySummary]:
        """Get recent memory summaries for a tenant."""
        result = await self.session.execute(
            select(MemorySummary)
            .where(MemorySummary.tenant_id == tenant_id)
            .order_by(desc(MemorySummary.created_at))
            .limit(limit)
        )
        return list(result.scalars().all())

    # --- Recall (simple keyword for now, vector search in RAG phase) ---

    async def recall(
        self,
        tenant_id: str,
        query: str,
        limit: int = 5,
    ) -> list[Message]:
        """Recall relevant messages across conversations (keyword match).
        Will be upgraded to vector similarity in Phase 2 (RAG).
        """
        # Simple ILIKE search for now
        result = await self.session.execute(
            select(Message)
            .join(Conversation)
            .where(
                Conversation.tenant_id == tenant_id,
                Message.content.ilike(f"%{query}%"),
            )
            .order_by(desc(Message.created_at))
            .limit(limit)
        )
        return list(result.scalars().all())
