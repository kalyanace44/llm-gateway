"""RAG service — document ingestion, chunking, embedding, and hybrid retrieval."""
from __future__ import annotations

import hashlib
import json
import math
import struct
from datetime import datetime, timezone
from typing import Optional

import httpx
import tiktoken
from sqlalchemy import select, desc, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.config import settings
from gateway.db.rag_models import KnowledgeBase, Document, Chunk


# --- Text chunking ---

class TextChunker:
    """Recursive character text splitter with overlap."""

    def __init__(self, chunk_size: int = 512, overlap: int = 64):
        self.chunk_size = chunk_size
        self.overlap = overlap
        self._enc = tiktoken.get_encoding("cl100k_base")

    def _token_count(self, text: str) -> int:
        return len(self._enc.encode(text))

    def chunk(self, text: str) -> list[dict]:
        """Split text into chunks with metadata."""
        # Split by paragraphs first, then sentences
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        chunks = []
        current_chunk = ""
        current_tokens = 0

        for para in paragraphs:
            para_tokens = self._token_count(para)

            # If single paragraph exceeds chunk size, split by sentences
            if para_tokens > self.chunk_size:
                if current_chunk:
                    chunks.append(current_chunk)
                    current_chunk = ""
                    current_tokens = 0
                # Split long paragraph by sentences
                sentences = self._split_sentences(para)
                for sent in sentences:
                    sent_tokens = self._token_count(sent)
                    if current_tokens + sent_tokens > self.chunk_size and current_chunk:
                        chunks.append(current_chunk)
                        # Overlap: keep last portion
                        overlap_text = self._get_overlap(current_chunk)
                        current_chunk = overlap_text + " " + sent if overlap_text else sent
                        current_tokens = self._token_count(current_chunk)
                    else:
                        current_chunk = (current_chunk + " " + sent).strip()
                        current_tokens += sent_tokens
            elif current_tokens + para_tokens > self.chunk_size and current_chunk:
                chunks.append(current_chunk)
                overlap_text = self._get_overlap(current_chunk)
                current_chunk = overlap_text + "\n\n" + para if overlap_text else para
                current_tokens = self._token_count(current_chunk)
            else:
                current_chunk = (current_chunk + "\n\n" + para).strip()
                current_tokens += para_tokens

        if current_chunk:
            chunks.append(current_chunk)

        return [
            {"content": c, "token_count": self._token_count(c), "index": i}
            for i, c in enumerate(chunks)
        ]

    def _split_sentences(self, text: str) -> list[str]:
        """Split text into sentences."""
        import re
        sentences = re.split(r'(?<=[.!?])\s+', text)
        return [s for s in sentences if s.strip()]

    def _get_overlap(self, text: str) -> str:
        """Get the last N tokens worth of text for overlap."""
        tokens = self._enc.encode(text)
        if len(tokens) <= self.overlap:
            return text
        overlap_tokens = tokens[-self.overlap:]
        return self._enc.decode(overlap_tokens)


# --- Embedding ---

class EmbeddingService:
    """Generate embeddings via OpenAI-compatible API."""

    def __init__(
        self,
        base_url: str = "http://localhost:8000/v1",
        api_key: str = "",
        model: str = "text-embedding-3-small",
        dimensions: int = 1536,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.dimensions = dimensions
        self._client = httpx.AsyncClient(timeout=60.0)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for a batch of texts."""
        # Batch in groups of 100
        all_embeddings = []
        for i in range(0, len(texts), 100):
            batch = texts[i:i + 100]
            resp = await self._client.post(
                f"{self.base_url}/embeddings",
                json={"input": batch, "model": self.model},
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            if resp.status_code == 200:
                data = resp.json()
                embeddings = [d["embedding"] for d in data["data"]]
                all_embeddings.extend(embeddings)
            else:
                # Fallback: generate random embeddings for dev/testing
                for _ in batch:
                    all_embeddings.append(self._random_embedding())
        return all_embeddings

    async def embed_single(self, text: str) -> list[float]:
        """Embed a single text."""
        results = await self.embed([text])
        return results[0] if results else self._random_embedding()

    def _random_embedding(self) -> list[float]:
        """Generate a normalized random embedding (dev fallback)."""
        import random
        vec = [random.gauss(0, 1) for _ in range(self.dimensions)]
        norm = math.sqrt(sum(x * x for x in vec))
        return [x / norm for x in vec]

    @staticmethod
    def serialize_embedding(embedding: list[float]) -> bytes:
        """Serialize embedding to bytes for storage."""
        return struct.pack(f'{len(embedding)}f', *embedding)

    @staticmethod
    def deserialize_embedding(data: bytes, dimensions: int = 1536) -> list[float]:
        """Deserialize embedding from bytes."""
        return list(struct.unpack(f'{dimensions}f', data))

    async def close(self):
        await self._client.aclose()


# --- Hybrid retrieval ---

class RetrievalService:
    """Hybrid retrieval: vector similarity + keyword (BM25-like) search with RRF merge."""

    def __init__(self, session: AsyncSession, embedding_svc: EmbeddingService):
        self.session = session
        self.embedding_svc = embedding_svc

    async def retrieve(
        self,
        query: str,
        kb_ids: list[str],
        top_k: int = 5,
        score_threshold: float = 0.0,
    ) -> list[dict]:
        """Hybrid retrieval: vector + keyword, merged via RRF."""
        # Get vector results
        vector_results = await self._vector_search(query, kb_ids, top_k=top_k * 4)

        # Get keyword results
        keyword_results = await self._keyword_search(query, kb_ids, top_k=top_k * 4)

        # Merge via Reciprocal Rank Fusion (RRF)
        merged = self._rrf_merge(vector_results, keyword_results, k=60)

        # Filter by score threshold and limit
        results = [r for r in merged if r["score"] >= score_threshold][:top_k]
        return results

    async def _vector_search(
        self, query: str, kb_ids: list[str], top_k: int = 20
    ) -> list[dict]:
        """Cosine similarity search against chunk embeddings."""
        query_embedding = await self.embedding_svc.embed_single(query)

        # Get all chunks for the knowledge bases
        result = await self.session.execute(
            select(Chunk)
            .where(Chunk.kb_id.in_(kb_ids), Chunk.embedding.is_not(None))
        )
        chunks = list(result.scalars().all())

        # Compute cosine similarity in Python (in prod: pgvector does this)
        scored = []
        for chunk in chunks:
            if chunk.embedding:
                chunk_emb = self.embedding_svc.deserialize_embedding(
                    chunk.embedding, self.embedding_svc.dimensions
                )
                similarity = self._cosine_similarity(query_embedding, chunk_emb)
                scored.append({
                    "chunk_id": chunk.id,
                    "document_id": chunk.document_id,
                    "content": chunk.content,
                    "score": similarity,
                    "metadata": chunk.metadata_ or {},
                    "source": "vector",
                })

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]

    async def _keyword_search(
        self, query: str, kb_ids: list[str], top_k: int = 20
    ) -> list[dict]:
        """Simple keyword search (BM25 approximation via LIKE matching)."""
        # Split query into terms
        terms = [t.strip().lower() for t in query.split() if len(t.strip()) > 2]
        if not terms:
            return []

        # Search for chunks containing any query term
        conditions = [Chunk.content.ilike(f"%{term}%") for term in terms]
        result = await self.session.execute(
            select(Chunk)
            .where(Chunk.kb_id.in_(kb_ids), or_(*conditions))
            .limit(top_k * 2)
        )
        chunks = list(result.scalars().all())

        # Score by term frequency (simple BM25 approximation)
        scored = []
        for chunk in chunks:
            content_lower = chunk.content.lower()
            term_hits = sum(1 for t in terms if t in content_lower)
            # Normalize score: hits / total_terms
            score = term_hits / len(terms) if terms else 0
            scored.append({
                "chunk_id": chunk.id,
                "document_id": chunk.document_id,
                "content": chunk.content,
                "score": score,
                "metadata": chunk.metadata_ or {},
                "source": "keyword",
            })

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]

    @staticmethod
    def _rrf_merge(
        vector_results: list[dict],
        keyword_results: list[dict],
        k: int = 60,
    ) -> list[dict]:
        """Reciprocal Rank Fusion — merge two ranked lists."""
        scores: dict[str, float] = {}
        docs: dict[str, dict] = {}

        for rank, doc in enumerate(vector_results):
            chunk_id = doc["chunk_id"]
            scores[chunk_id] = scores.get(chunk_id, 0) + 1.0 / (k + rank + 1)
            docs[chunk_id] = doc

        for rank, doc in enumerate(keyword_results):
            chunk_id = doc["chunk_id"]
            scores[chunk_id] = scores.get(chunk_id, 0) + 1.0 / (k + rank + 1)
            docs[chunk_id] = doc

        # Sort by RRF score
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        results = []
        for chunk_id, rrf_score in ranked:
            doc = docs[chunk_id].copy()
            doc["score"] = round(rrf_score, 6)
            doc["source"] = "hybrid"
            results.append(doc)

        return results

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        """Compute cosine similarity between two vectors."""
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)


# --- RAG Service (orchestrator) ---

class RAGService:
    """Orchestrates document ingestion, chunking, embedding, and retrieval."""

    def __init__(self, session: AsyncSession, embedding_svc: EmbeddingService):
        self.session = session
        self.embedding_svc = embedding_svc
        self.chunker = TextChunker(chunk_size=512, overlap=64)
        self.retrieval = RetrievalService(session, embedding_svc)

    # --- Knowledge Base CRUD ---

    async def create_knowledge_base(
        self,
        tenant_id: str,
        name: str,
        description: str | None = None,
        config: dict | None = None,
    ) -> KnowledgeBase:
        """Create a new knowledge base."""
        kb = KnowledgeBase(
            tenant_id=tenant_id,
            name=name,
            description=description,
            config=config or {"chunk_size": 512, "overlap": 64},
        )
        self.session.add(kb)
        await self.session.commit()
        await self.session.refresh(kb)
        return kb

    async def list_knowledge_bases(self, tenant_id: str) -> list[KnowledgeBase]:
        """List knowledge bases for a tenant."""
        result = await self.session.execute(
            select(KnowledgeBase)
            .where(KnowledgeBase.tenant_id == tenant_id, KnowledgeBase.status != "deleted")
            .order_by(desc(KnowledgeBase.created_at))
        )
        return list(result.scalars().all())

    async def get_knowledge_base(self, kb_id: str, tenant_id: str) -> Optional[KnowledgeBase]:
        """Get a knowledge base (tenant-scoped)."""
        result = await self.session.execute(
            select(KnowledgeBase).where(
                KnowledgeBase.id == kb_id,
                KnowledgeBase.tenant_id == tenant_id,
            )
        )
        return result.scalar_one_or_none()

    async def delete_knowledge_base(self, kb_id: str, tenant_id: str) -> bool:
        """Soft-delete a knowledge base."""
        kb = await self.get_knowledge_base(kb_id, tenant_id)
        if not kb:
            return False
        kb.status = "deleted"
        await self.session.commit()
        return True

    # --- Document ingestion ---

    async def ingest_document(
        self,
        kb_id: str,
        content: str,
        filename: str | None = None,
        source_url: str | None = None,
        metadata: dict | None = None,
    ) -> Document:
        """Ingest a document: chunk, embed, and store."""
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]

        # Create document record
        doc = Document(
            kb_id=kb_id,
            filename=filename,
            source_url=source_url,
            content_hash=content_hash,
            doc_metadata=metadata or {},
            status="processing",
        )
        self.session.add(doc)
        await self.session.commit()
        await self.session.refresh(doc)

        try:
            # Chunk the document
            chunks_data = self.chunker.chunk(content)

            # Embed all chunks
            texts = [c["content"] for c in chunks_data]
            embeddings = await self.embedding_svc.embed(texts)

            # Store chunks
            total_tokens = 0
            for chunk_data, embedding in zip(chunks_data, embeddings):
                chunk = Chunk(
                    document_id=doc.id,
                    kb_id=kb_id,
                    content=chunk_data["content"],
                    embedding=self.embedding_svc.serialize_embedding(embedding),
                    chunk_index=chunk_data["index"],
                    token_count=chunk_data["token_count"],
                    metadata_=metadata or {},
                )
                self.session.add(chunk)
                total_tokens += chunk_data["token_count"]

            # Update document status
            doc.status = "ready"
            doc.chunk_count = len(chunks_data)
            doc.total_tokens = total_tokens
            doc.processed_at = datetime.now(timezone.utc)

            # Update knowledge base counts
            result = await self.session.execute(
                select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
            )
            kb = result.scalar_one_or_none()
            if kb:
                kb.document_count = (kb.document_count or 0) + 1
                kb.chunk_count = (kb.chunk_count or 0) + len(chunks_data)

            await self.session.commit()
            await self.session.refresh(doc)

        except Exception as e:
            doc.status = "failed"
            doc.error_message = str(e)
            await self.session.commit()
            raise

        return doc

    # --- Retrieval ---

    async def retrieve(
        self,
        tenant_id: str,
        query: str,
        kb_ids: list[str] | None = None,
        top_k: int = 5,
        score_threshold: float = 0.0,
    ) -> list[dict]:
        """Retrieve relevant chunks from tenant's knowledge bases."""
        # If no specific KBs, use all active ones for the tenant
        if not kb_ids:
            kbs = await self.list_knowledge_bases(tenant_id)
            kb_ids = [kb.id for kb in kbs if kb.status == "active"]

        if not kb_ids:
            return []

        return await self.retrieval.retrieve(
            query=query,
            kb_ids=kb_ids,
            top_k=top_k,
            score_threshold=score_threshold,
        )

    async def get_document(self, doc_id: str) -> Optional[Document]:
        """Get document by ID."""
        result = await self.session.execute(
            select(Document).where(Document.id == doc_id)
        )
        return result.scalar_one_or_none()
