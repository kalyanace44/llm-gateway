# Production LLM Gateway

A production-grade LLM gateway that routes, observes, and controls multi-model inference at scale. Demonstrates end-to-end AI deployment skills for FDE roles.

## Architecture

```
Clients → FastAPI Gateway → [Router] → Backend Models (vLLM, Ollama, OpenAI, Anthropic)
                ↓                              ↓
         [Middleware]                    [Health Checks]
         - Auth (API keys)
         - Rate Limiting
         - Cost Tracking
         - Request Logging
                ↓
         [Observability]
         - Prometheus metrics
         - Grafana dashboards
         - W&B experiment tracking
```

## Features

### Core
- OpenAI-compatible API (drop-in replacement)
- Multi-backend routing (vLLM, Ollama, OpenAI, Anthropic, custom)
- Model registry with health checks and warm/cold status
- Streaming support (SSE)

### Traffic Control
- A/B routing (split traffic between models by percentage)
- Fallback chains (if model A fails → try B → try C)
- Priority queues (paid users get GPU priority)
- Rate limiting per API key (token bucket)
- Request/response caching (semantic dedup)

### Cost & Usage
- Per-request token counting and cost attribution
- Per-key usage tracking with budgets and alerts
- Model cost comparison dashboard
- Estimated vs actual cost reconciliation

### Observability
- Prometheus metrics: latency p50/p95/p99, TTFT, tokens/sec, error rates
- Grafana dashboards (pre-built)
- Request tracing (OpenTelemetry)
- Quality scoring (optional W&B integration)

### Deployment
- Docker Compose (local dev)
- Helm chart (K8s production)
- Terraform module (AWS EKS with GPU nodes)
- Auto-scaling based on queue depth

## Tech Stack
- **Gateway**: Python + FastAPI + uvicorn
- **Storage**: PostgreSQL (usage/keys), Redis (cache/rate-limits)
- **Metrics**: Prometheus + Grafana
- **Infra**: Docker, K8s, Terraform (AWS CDK optional)
- **Models**: vLLM (self-hosted), plus proxy to OpenAI/Anthropic

## Project Structure
```
llm-gateway/
├── gateway/              # FastAPI application
│   ├── main.py          # App entry + lifespan
│   ├── api/             # Route handlers
│   │   ├── chat.py      # /v1/chat/completions
│   │   ├── models.py    # /v1/models
│   │   └── admin.py     # /admin/* (keys, config)
│   ├── core/            # Business logic
│   │   ├── router.py    # Model selection + A/B + fallback
│   │   ├── backends.py  # Backend adapters (vLLM, OpenAI, etc.)
│   │   ├── auth.py      # API key validation
│   │   ├── limiter.py   # Rate limiting
│   │   ├── costs.py     # Token counting + cost calc
│   │   └── cache.py     # Semantic response cache
│   ├── models/          # Pydantic schemas
│   └── config.py        # Settings (from env/yaml)
├── infra/
│   ├── docker/          # Dockerfiles
│   ├── helm/            # K8s Helm chart
│   ├── terraform/       # AWS EKS + GPU
│   └── docker-compose.yml
├── dashboards/          # Grafana JSON
├── tests/
├── pyproject.toml
└── README.md
```

## Milestones
1. **Core gateway** — routes to multiple backends, OpenAI-compatible API
2. **Auth + rate limiting** — API keys, token bucket, Redis-backed
3. **Cost tracking** — per-request attribution, PostgreSQL storage
4. **A/B routing + fallbacks** — traffic splitting, automatic failover
5. **Observability** — Prometheus metrics, Grafana dashboard
6. **Deployment** — Docker Compose → Helm chart → Terraform
7. **Demo** — Load test, show dashboards, write blog post
