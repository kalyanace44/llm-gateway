# AI Operations Platform — Technical Specification

## Overview

Evolution of the LLM Gateway into a multi-tenant AI Operations Platform with persistent memory, RAG, self-healing infrastructure, and automated ML deployment pipelines. Designed for enterprises running AI workloads at scale with per-customer isolation.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                         AI Operations Platform                                   │
│                                                                                  │
│  ┌──────────────────────────────────────────────────────────────────────────┐   │
│  │                        Control Plane                                      │   │
│  │  ┌─────────┐  ┌─────────────┐  ┌──────────┐  ┌───────────────────────┐  │   │
│  │  │ Tenant  │  │   Model     │  │ Pipeline │  │  Experiment Tracker   │  │   │
│  │  │ Manager │  │  Registry   │  │ Orchestr │  │  (A/B, canary, shadow)│  │   │
│  │  └─────────┘  └─────────────┘  └──────────┘  └───────────────────────┘  │   │
│  └──────────────────────────────────────────────────────────────────────────┘   │
│                                                                                  │
│  ┌──────────────────────────────────────────────────────────────────────────┐   │
│  │                        Data Plane                                         │   │
│  │  ┌─────────┐  ┌─────────────┐  ┌──────────┐  ┌───────────────────────┐  │   │
│  │  │  Auth   │  │   Router    │  │ Backends │  │    Self-Healing       │  │   │
│  │  │+ Limits │  │ (A/B + FB)  │  │(vLLM,OAI)│  │ (circuit break,drift)│  │   │
│  │  └─────────┘  └─────────────┘  └──────────┘  └───────────────────────┘  │   │
│  └──────────────────────────────────────────────────────────────────────────┘   │
│                                                                                  │
│  ┌──────────────────────────────────────────────────────────────────────────┐   │
│  │                       Intelligence Layer                                  │   │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────┐  │   │
│  │  │ Persistent Mem  │  │   RAG Engine    │  │   Embedding Pipeline    │  │   │
│  │  │ (conversations) │  │ (hybrid search) │  │ (ingest, chunk, index)  │  │   │
│  │  └─────────────────┘  └─────────────────┘  └─────────────────────────┘  │   │
│  └──────────────────────────────────────────────────────────────────────────┘   │
│                                                                                  │
│  ┌──────────────────────────────────────────────────────────────────────────┐   │
│  │                       Storage Layer                                        │   │
│  │  PostgreSQL + pgvector │ Redis │ S3 (docs/models) │ Prometheus + Grafana  │   │
│  └──────────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Module 1: Persistent Memory

### Purpose
Per-customer conversation memory that persists across sessions. Enables context-aware responses without re-sending full history.

### Data Model

```sql
-- Tenants
CREATE TABLE tenants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    slug VARCHAR(64) UNIQUE NOT NULL,
    config JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Conversations (per-tenant)
CREATE TABLE conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES tenants(id),
    session_id VARCHAR(255) NOT NULL,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Messages
CREATE TABLE messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID REFERENCES conversations(id),
    role VARCHAR(20) NOT NULL, -- user, assistant, system, tool
    content TEXT NOT NULL,
    token_count INTEGER,
    embedding vector(1536),  -- pgvector
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Memory summaries (distilled from long conversations)
CREATE TABLE memory_summaries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES tenants(id),
    conversation_id UUID REFERENCES conversations(id),
    summary TEXT NOT NULL,
    embedding vector(1536),
    message_range INT4RANGE, -- which messages this covers
    created_at TIMESTAMPTZ DEFAULT now()
);
```

### API Additions

```
POST   /v1/memory/conversations          — Start new conversation
GET    /v1/memory/conversations/{id}      — Get conversation history
POST   /v1/memory/conversations/{id}/messages — Append message
GET    /v1/memory/recall?query=...&tenant=... — Semantic recall across conversations
POST   /v1/memory/summarize/{conv_id}     — Trigger conversation summarization
```

### Behavior
- Chat completions auto-store messages when `X-Tenant-ID` header present
- Long conversations (>20 messages) auto-summarize older messages
- Semantic recall: find relevant past context using embedding similarity
- Per-tenant isolation: queries never cross tenant boundaries

---

## Module 2: RAG Engine

### Purpose
Customer-specific knowledge bases with hybrid retrieval (vector + BM25 keyword search). Each tenant uploads docs → system chunks, embeds, indexes → retrieval augments completions.

### Data Model

```sql
-- Knowledge bases (per-tenant)
CREATE TABLE knowledge_bases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES tenants(id),
    name VARCHAR(255) NOT NULL,
    description TEXT,
    config JSONB DEFAULT '{}', -- chunk_size, overlap, embedding_model
    status VARCHAR(20) DEFAULT 'active',
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Documents
CREATE TABLE documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kb_id UUID REFERENCES knowledge_bases(id),
    filename VARCHAR(512),
    source_url TEXT,
    content_hash VARCHAR(64),
    doc_metadata JSONB DEFAULT '{}',
    status VARCHAR(20) DEFAULT 'processing', -- processing, ready, failed
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Chunks (the retrieval units)
CREATE TABLE chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID REFERENCES documents(id),
    kb_id UUID REFERENCES knowledge_bases(id),
    content TEXT NOT NULL,
    embedding vector(1536),
    chunk_index INTEGER,
    token_count INTEGER,
    metadata JSONB DEFAULT '{}', -- page, section, heading
    created_at TIMESTAMPTZ DEFAULT now()
);

-- BM25 index via pg_trgm + tsvector
ALTER TABLE chunks ADD COLUMN tsv tsvector
    GENERATED ALWAYS AS (to_tsvector('english', content)) STORED;
CREATE INDEX idx_chunks_tsv ON chunks USING GIN (tsv);
CREATE INDEX idx_chunks_embedding ON chunks USING ivfflat (embedding vector_cosine_ops);
```

### Retrieval Pipeline

```
Query → [Embed query] → [Vector search (top 20)] ─┐
                                                     ├─ RRF merge → top 5 → LLM context
Query → [BM25 keyword search (top 20)] ────────────┘
```

### API

```
POST   /v1/rag/knowledge-bases                  — Create KB
POST   /v1/rag/knowledge-bases/{id}/documents   — Upload document (PDF, MD, TXT, HTML)
GET    /v1/rag/knowledge-bases/{id}/status       — Ingestion progress
POST   /v1/rag/retrieve                          — Retrieve relevant chunks
DELETE /v1/rag/knowledge-bases/{id}              — Delete KB + all chunks
```

### Integration with Chat
When `rag_config` is present in chat completion request:
```json
{
  "model": "llama-3.1-70b",
  "messages": [...],
  "rag_config": {
    "knowledge_base_ids": ["kb-uuid-1", "kb-uuid-2"],
    "top_k": 5,
    "score_threshold": 0.7,
    "include_sources": true
  }
}
```

The gateway:
1. Extracts the latest user message
2. Retrieves top-K chunks via hybrid search
3. Injects as system context before forwarding to backend
4. Returns source attributions in response metadata

---

## Module 3: Self-Healing

### Components

#### Circuit Breakers (per-backend)
```python
class CircuitBreaker:
    states: CLOSED → OPEN → HALF_OPEN
    thresholds:
        failure_count: 5         # consecutive failures to trip
        timeout: 60s             # time in OPEN before trying HALF_OPEN
        success_count: 3         # successes in HALF_OPEN to close
    metrics:
        failure_rate, latency_p99, error_types
```

#### Health Monitor
- Active probes every 30s (lightweight /health or /v1/models call)
- Passive monitoring (track actual request outcomes)
- Latency drift detection: alert if p99 > 2x baseline over 5min window
- Automatic backend demotion/promotion based on health score

#### Auto-Recovery Actions
| Signal | Action |
|--------|--------|
| Backend 5xx > 5 consecutive | Circuit breaker OPEN, route to fallback |
| Latency p99 > 2x baseline | Emit warning metric, increase weight on faster backend |
| All backends unhealthy | Return 503 with retry-after header, queue requests |
| Model quality drift (BLEU/BERTScore drop) | Shadow-route to canary, alert ops |
| OOM / GPU error from vLLM | Trigger pod restart via K8s API, route to fallback |

#### Drift Detection
- Sample 1% of requests → run quality evaluation (BERTScore vs reference)
- Store rolling 24h quality scores per model
- Alert when quality drops >10% from 7-day baseline
- Auto-trigger rollback to previous model version if canary fails

### API

```
GET  /v1/health/backends           — Backend health with circuit breaker state
GET  /v1/health/drift              — Quality drift scores
POST /v1/health/backends/{name}/reset — Force reset circuit breaker
```

---

## Module 4: Multi-Tenant Isolation

### Tenant Configuration

```yaml
# Per-tenant config (stored in tenants.config JSONB)
tenant:
  slug: acme-corp
  tier: enterprise          # free, pro, enterprise
  
  # Model access
  allowed_models: ["llama-3.1-70b", "gpt-4o"]
  default_model: "llama-3.1-70b"
  
  # Routing preferences
  routing:
    strategy: "lowest-latency"  # round-robin, lowest-latency, cost-optimized
    fallback_chain: [vllm-primary, openai-fallback]
  
  # Limits
  limits:
    requests_per_minute: 60
    tokens_per_minute: 200000
    monthly_budget_usd: 500.00
    max_knowledge_bases: 10
    max_documents_per_kb: 1000
    storage_gb: 50
  
  # Memory
  memory:
    enabled: true
    auto_summarize: true
    retention_days: 90
```

### Isolation Guarantees
- Row-level security in PostgreSQL (tenant_id on every table)
- Separate embedding namespaces (no cross-tenant retrieval possible)
- Per-tenant rate limiting (independent buckets)
- Per-tenant cost tracking and budget enforcement
- Per-tenant model access control

---

## Module 5: Automated Deployment Pipeline

### GitOps Model Deployment

```
┌──────────┐    ┌───────────┐    ┌─────────────┐    ┌──────────┐    ┌────────┐
│ Model    │───▶│  CI/CD    │───▶│  Canary     │───▶│  Shadow  │───▶│  Full  │
│ Registry │    │ (GH Act.) │    │  (5% traf.) │    │  (eval)  │    │ Deploy │
└──────────┘    └───────────┘    └─────────────┘    └──────────┘    └────────┘
                     │                   │                │
                     ▼                   ▼                ▼
              Build container    Monitor metrics    Compare quality
              Push to ECR        5min bake time     Auto-rollback if
              Update manifest    Auto-rollback       BLEU < threshold
```

### Pipeline Stages

1. **Model Registration** — Push new model/version to registry (S3 + DynamoDB metadata)
2. **Container Build** — Auto-build vLLM serving container with new model weights
3. **Canary Deploy** — Route 5% traffic to new version, monitor for 5min
4. **Shadow Evaluation** — Run eval suite (BERTScore, latency, cost) against shadow traffic
5. **Promotion/Rollback** — Auto-promote if quality ≥ baseline, else rollback

### API

```
POST   /v1/deployments                  — Trigger new deployment
GET    /v1/deployments/{id}/status       — Deployment progress
POST   /v1/deployments/{id}/rollback     — Force rollback
GET    /v1/deployments/history           — Deployment history

POST   /v1/models/register              — Register model version
GET    /v1/models/registry              — List registered models + versions
POST   /v1/models/{id}/promote          — Promote canary to production
```

### Model Registry Schema

```sql
CREATE TABLE model_registry (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,        -- e.g. "llama-3.1-70b"
    version VARCHAR(64) NOT NULL,       -- e.g. "v2-finetuned-2024-01"
    artifact_uri TEXT NOT NULL,         -- s3://models/llama-3.1-70b/v2/
    serving_config JSONB NOT NULL,      -- tensor_parallel, quantization, max_batch
    status VARCHAR(20) DEFAULT 'registered', -- registered, deploying, canary, production, retired
    quality_scores JSONB DEFAULT '{}',  -- bertscore, bleu, latency_p50, cost_per_1k
    created_at TIMESTAMPTZ DEFAULT now(),
    promoted_at TIMESTAMPTZ,
    UNIQUE(name, version)
);

CREATE TABLE deployments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    model_id UUID REFERENCES model_registry(id),
    strategy VARCHAR(20) NOT NULL,     -- canary, blue-green, shadow
    traffic_pct REAL DEFAULT 0.05,
    status VARCHAR(20) DEFAULT 'pending', -- pending, deploying, baking, evaluating, promoted, rolled_back
    metrics JSONB DEFAULT '{}',
    started_at TIMESTAMPTZ DEFAULT now(),
    completed_at TIMESTAMPTZ
);
```

---

## Module 6: ML + Customer Sync (Experiment Tracking)

### Purpose
Run experiments (model versions, prompts, parameters) per-customer and track outcomes. Enables data-driven model selection.

### Experiments

```sql
CREATE TABLE experiments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    description TEXT,
    tenant_ids UUID[] DEFAULT '{}',     -- which tenants participate (empty = all)
    config JSONB NOT NULL,
    -- config example:
    -- {
    --   "variants": [
    --     {"name": "control", "model": "llama-3.1-70b-v1", "weight": 0.5},
    --     {"name": "treatment", "model": "llama-3.1-70b-v2-ft", "weight": 0.5}
    --   ],
    --   "metrics": ["latency", "user_rating", "cost"],
    --   "duration_hours": 168,
    --   "min_samples": 1000
    -- }
    status VARCHAR(20) DEFAULT 'draft', -- draft, running, completed, cancelled
    results JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now(),
    ended_at TIMESTAMPTZ
);

CREATE TABLE experiment_events (
    id BIGSERIAL PRIMARY KEY,
    experiment_id UUID REFERENCES experiments(id),
    variant VARCHAR(64),
    tenant_id UUID,
    request_id UUID,
    metrics JSONB NOT NULL,  -- {latency: 1.2, tokens: 450, cost: 0.003, user_rating: null}
    created_at TIMESTAMPTZ DEFAULT now()
);
```

### API

```
POST   /v1/experiments                  — Create experiment
GET    /v1/experiments/{id}             — Get experiment + results
POST   /v1/experiments/{id}/start       — Start experiment
POST   /v1/experiments/{id}/stop        — End experiment early
GET    /v1/experiments/{id}/results     — Statistical analysis (confidence intervals)
POST   /v1/experiments/{id}/events      — Record outcome event (user feedback)
```

### Integration
- Router checks active experiments before routing
- If tenant is in an experiment → route per variant weights
- All requests in experiment log metrics to experiment_events
- Statistical significance calculated on-demand (Bayesian or frequentist)

---

## Implementation Order

| Phase | Modules | Effort | FDE Signal |
|-------|---------|--------|------------|
| **Phase 1** | Persistent Memory + Multi-Tenant | 1 session | High — shows data architecture |
| **Phase 2** | RAG Engine | 1 session | Very High — most asked about in interviews |
| **Phase 3** | Self-Healing | 1 session | High — shows production thinking |
| **Phase 4** | Deployment Pipeline + Model Registry | 1 session | Very High — MLOps core |
| **Phase 5** | Experiment Tracking + ML Sync | 1 session | High — data-driven decisions |

---

## Tech Stack Additions

| Component | Technology | Why |
|-----------|-----------|-----|
| Database | PostgreSQL 16 + pgvector | Embeddings + relational in one DB |
| Cache/Queue | Redis 7 | Rate limiting, job queues, session cache |
| Object Store | S3 (or MinIO local) | Documents, model artifacts |
| Embeddings | OpenAI text-embedding-3-small or local (sentence-transformers) | 1536-dim, fast |
| Chunking | LangChain text splitters | Recursive character + semantic |
| PDF parsing | pymupdf / unstructured | Document ingestion |
| Background jobs | Celery + Redis (or arq) | Async embedding, summarization |
| Migrations | Alembic | Schema versioning |
| Container registry | ECR | Model serving containers |
| GitOps | ArgoCD or Flux | K8s declarative deployments |

---

## Non-Functional Requirements

- **Latency**: RAG retrieval < 200ms p99 (warm), memory recall < 100ms
- **Throughput**: 500 req/s at gateway layer (pre-LLM routing)
- **Storage**: 100GB per tenant knowledge base
- **Availability**: 99.9% gateway uptime, graceful degradation if RAG/memory unavailable
- **Security**: Tenant isolation verified by automated tests, no cross-tenant data leaks
- **Observability**: Every module emits Prometheus metrics, structured logs, trace spans
