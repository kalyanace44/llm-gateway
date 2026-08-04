# Prism

**AI Traffic Control Plane — route, protect, optimize, observe.**

Every AI call flows through Prism. Drop-in OpenAI SDK-compatible proxy that gives you multi-provider routing, caching, security scanning, cost governance, and compliance — without changing application code.

![Architecture](docs/architecture.svg)

## Why

| Problem | Solution |
|---------|----------|
| **$500K/mo LLM costs** with zero attribution | Per-team cost tracking, budget enforcement, cheaper-model substitution |
| **15 models, 5 providers**, no governance | Centralized registry, quality-gated promotion, consistent failover |
| **2-5% API failure rate** | Circuit breakers, cross-provider fallback, auto-recovery |
| **SOC2/HIPAA compliance** gap | Full audit trail, data residency controls, export |
| **PII leakage** in prompts | Inline scanning before requests leave your network |
| **Offline evals miss regressions** | Continuous quality monitoring on production traffic |

## Quickstart

```bash
pip install prism-gateway

# Configure providers
cat > prism.yaml << 'EOF'
admin_key: ${PRISM_ADMIN_KEY}
port: 8000
providers:
  - name: openai
    base_url: https://api.openai.com/v1
    api_key: ${OPENAI_API_KEY}
    models: [gpt-4o, gpt-4o-mini]
    priority: 0
  - name: anthropic
    base_url: https://api.anthropic.com/v1
    api_key: ${ANTHROPIC_API_KEY}
    models: [claude-sonnet-4-20250514]
    priority: 1
EOF

# Start
prism serve
```

Your application code stays the same — just change `base_url`:

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="your-prism-key",
)

response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Hello!"}],
)
```

## Features

### OSS Core (Apache 2.0)

- **OpenAI-compatible API** — drop-in `/v1/chat/completions`, `/v1/models`
- **Multi-provider routing** — priority-based, weighted load balancing, fallback chains
- **Circuit breakers** — per-provider failure isolation, auto-recovery (closed → open → half-open)
- **Response caching** — exact-match LRU with TTL, cache hit/miss metrics
- **API key management** — per-team keys, token bucket rate limiting, budget caps
- **Prometheus metrics** — requests, latency (p50/p95/p99), tokens, costs, errors
- **Cost attribution** — per-team, per-model spend tracking
- **Admin API** — key CRUD, cache control, provider health, circuit reset

### Prism Cloud (Paid)

- **Continuous eval** — detect quality regressions on production traffic with statistical significance
- **PII scanning** — block sensitive data before it reaches providers
- **Prompt injection detection** — classify and block adversarial inputs
- **Cost optimizer** — recommend cheaper models that meet your quality threshold
- **Compliance dashboard** — SOC2/HIPAA audit export, data residency
- **Multi-cluster federation** — single pane across environments
- **A/B testing** — traffic splitting with auto-promote on significance

## API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/chat/completions` | POST | OpenAI-compatible chat (routes to best provider) |
| `/v1/models` | GET | List available models across providers |
| `/health` | GET | Liveness probe |
| `/ready` | GET | Readiness probe (checks provider health) |
| `/health/providers` | GET | Circuit breaker status per provider |
| `/metrics` | GET | Prometheus metrics |
| `/admin/keys` | POST/GET/DELETE | Manage API keys |
| `/admin/stats` | GET | Platform statistics |
| `/admin/cache/invalidate` | POST | Clear response cache |
| `/admin/providers/{name}/reset` | POST | Reset circuit breaker |

## Deployment

### Kubernetes (Production)

```bash
kubectl apply -f deploy/k8s/
```

Includes:
- Deployment with rolling updates (maxUnavailable: 0)
- HPA scaling 2→50 pods on CPU + request rate
- PodDisruptionBudget (minAvailable: 2)
- Readiness/liveness probes
- ConfigMap + Secrets separation
- ServiceAccount with least privilege

### Docker

```bash
docker build -t prism-gateway .
docker run -p 8000:8000 -v ./prism.yaml:/etc/prism/prism.yaml prism-gateway
```

### Dev Mode

```bash
prism serve --dev --port 8000
```

## Architecture

```
Client → Prism Gateway → [Auth] → [Cache] → [Router] → Provider A (primary)
                                                    ↘ Provider B (fallback)
                                                    ↘ Provider C (fallback)
         ↕ Circuit Breakers    ↕ Metrics    ↕ Cost Tracking
```

Each request:
1. **Auth** — validate API key, check rate limit + budget
2. **Cache** — return cached response if available
3. **Route** — pick provider by priority, skip if circuit is open
4. **Execute** — forward to provider with connection pooling
5. **Record** — log metrics, cost, update circuit breaker state
6. **Fallback** — on failure, try next provider in chain

## Configuration

See `prism.yaml` for all options:

```yaml
host: 0.0.0.0
port: 8000
workers: 4
admin_key: your-admin-key

providers:
  - name: openai
    base_url: https://api.openai.com/v1
    api_key: sk-...
    models: [gpt-4o, gpt-4o-mini]
    priority: 0
    timeout: 60.0
    max_retries: 2

rate_limit:
  requests_per_minute: 600
  tokens_per_minute: 1000000

cache:
  enabled: true
  ttl_seconds: 3600
  max_entries: 50000

resilience:
  failure_threshold: 5
  recovery_timeout_seconds: 60
  success_threshold: 3
```

## Tests

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

## License

- Core gateway (`prism/`): Apache 2.0
- Cloud features (`prism_cloud/`): Proprietary
