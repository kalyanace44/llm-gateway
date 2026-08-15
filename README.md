# Prism

**AI Traffic Control Plane — route, protect, optimize, observe.**

> The only LLM gateway built for Indian fintech compliance. PAN/Aadhaar blocking, RBI audit trails, and cost governance — out of the box.

Every AI call flows through Prism. Drop-in OpenAI SDK-compatible proxy that gives you multi-provider routing, semantic caching, PII scanning, cost governance, and compliance — without changing a single line of application code.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          Your Application Code                               │
│                      (OpenAI SDK, LangChain, CrewAI)                         │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │  ← just change base_url
┌──────────────────────────────────▼──────────────────────────────────────────┐
│                              PRISM GATEWAY                                    │
│                                                                              │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────────┐  │
│  │  Auth +  │ │ PII/Injn │ │  Smart   │ │ Semantic │ │  Observability   │  │
│  │  Limits  │ │  Scanner │ │  Router  │ │  Cache   │ │  + Cost Track    │  │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────────────┘  │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────────┐  │
│  │ Circuit  │ │   A/B    │ │Continuous│ │   Cost   │ │   Compliance     │  │
│  │ Breakers │ │  Testing │ │   Eval   │ │ Optimizer│ │   + Audit Log    │  │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────────────┘  │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │
        ┌──────────┬───────────────┼───────────────┬──────────────┐
        ▼          ▼               ▼               ▼              ▼
   ┌─────────┐ ┌─────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐
   │ OpenAI  │ │Anthropic│ │  Google  │ │  Azure   │ │  Self-hosted │
   │ GPT-4o  │ │ Claude  │ │  Gemini  │ │ OpenAI   │ │  vLLM/Ollama │
   └─────────┘ └─────────┘ └──────────┘ └──────────┘ └──────────────┘
```

## Why Prism Exists

Indian fintechs using AI face a unique combination of problems that no existing gateway solves:

| Problem | What happens today | How Prism fixes it |
|---------|-------------------|-------------------|
| **PII in prompts** | Engineers send PAN/Aadhaar/CC numbers to OpenAI | Inline scanner blocks before data leaves your VPC |
| **RBI compliance** | No audit trail of AI decisions affecting transactions | Full request/response logging with data residency |
| **Cost explosion** | ₹20-50L/month with zero attribution per team | Per-team budgets, automatic cheaper-model routing |
| **Provider outages** | OpenAI goes down → your payment flow breaks | Circuit breakers + automatic cross-provider fallback |
| **Model sprawl** | 15 models, 5 providers, no governance | Centralized registry, A/B testing, quality-gated promotion |
| **Eval gap** | Offline benchmarks pass, production quality degrades | Continuous eval on real traffic with statistical significance |

**No other gateway offers India-specific compliance (PAN/Aadhaar/UPI blocking) + self-hosted + full observability in a single Helm install.**

## Quick Start

### Option 1: pip (Development)

```bash
pip install prism-gateway

# Create config
cat > prism.yaml << 'EOF'
port: 8000
admin_key: change-me-in-production

providers:
  - name: openai
    base_url: https://api.openai.com/v1
    api_key: ${OPENAI_API_KEY}
    models: [gpt-4o, gpt-4o-mini]
    priority: 0

  - name: anthropic
    base_url: https://api.anthropic.com/v1
    api_key: ${ANTHROPIC_API_KEY}
    models: [claude-sonnet-4-20250514, claude-haiku-3]
    priority: 1

rate_limit:
  requests_per_minute: 600
  tokens_per_minute: 1000000

cache:
  enabled: true
  ttl_seconds: 3600

scanner:
  enabled: true
  rules:
    - name: pan_card
      pattern: '[A-Z]{5}[0-9]{4}[A-Z]'
      action: block
    - name: aadhaar
      pattern: '\b\d{4}\s\d{4}\s\d{4}\b'
      action: block
    - name: credit_card
      pattern: '\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b'
      action: block

resilience:
  failure_threshold: 5
  recovery_timeout_seconds: 60
  success_threshold: 3
EOF

# Start the gateway
prism serve

# Test it
curl http://localhost:8000/health
```

### Option 2: Docker (Staging)

```bash
docker build -t prism-gateway .
docker run -p 8000:8000 \
  -v ./prism.yaml:/etc/prism/prism.yaml \
  -e OPENAI_API_KEY=sk-... \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  prism-gateway
```

### Option 3: Docker Compose (Full Stack)

```bash
cd infra/
cp .env.example .env   # Add API keys
docker-compose up -d   # Gateway + Redis + Prometheus + Grafana
```

Services:
- Gateway: http://localhost:8080
- Prometheus: http://localhost:9090
- Grafana: http://localhost:3000 (admin/admin)

### Option 4: Helm Chart (Production)

```bash
# Add the Prism Helm repository
helm repo add prism https://kalyanace44.github.io/llm-gateway/charts
helm repo update

# Install everything in one command
helm install prism prism/prism \
  --namespace prism --create-namespace \
  --set secrets.openaiApiKey=sk-... \
  --set secrets.anthropicApiKey=sk-ant-...
```

This deploys:
- Prism Gateway (2 pods, HPA 2→10, liveness/readiness probes)
- Redis 19.x (Bitnami — caching + rate limiting)
- Prometheus 25.x (metrics scraping)
- Grafana 8.x (dashboards, auto-wired to Prometheus)
- Ingress (optional, nginx)

```bash
# Verify
kubectl port-forward svc/prism-prism 8000:8000 -n prism
curl http://localhost:8000/health

# Customize
helm show values prism/prism > my-values.yaml
# Edit my-values.yaml...
helm upgrade prism prism/prism -f my-values.yaml -n prism
```

## Use With Your App

Zero code changes — just change `base_url`:

```python
from openai import OpenAI

# Before (direct to OpenAI)
# client = OpenAI(api_key="sk-...")

# After (through Prism — same API, all features enabled)
client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="your-prism-key",
)

response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Analyze this loan application"}],
)
```

Works with: OpenAI SDK, LangChain, LlamaIndex, CrewAI, Haystack, or any OpenAI-compatible client.

## Features

### Core (Apache 2.0 — Free Forever)

| Feature | Description |
|---------|-------------|
| **OpenAI-compatible API** | Drop-in `/v1/chat/completions`, `/v1/models`, `/v1/embeddings` |
| **10+ Provider Support** | OpenAI, Anthropic, Google Gemini, Azure OpenAI, AWS Bedrock, Mistral, Groq, Together, Fireworks, vLLM/Ollama |
| **Smart Routing** | Priority-based, weighted load balancing, least-latency, cost-optimized |
| **Circuit Breakers** | Per-provider failure isolation, auto-recovery (closed → open → half-open) |
| **Semantic Caching** | Embedding-based similarity matching — "How do I deploy?" hits cache for "What's the deployment process?" |
| **PII Scanner** | Block PAN, Aadhaar, credit cards, phone numbers, emails before they reach providers |
| **Prompt Injection Detection** | Pattern-based + classifier blocking of adversarial inputs |
| **Rate Limiting** | Token bucket per API key, per-team, per-model with burst support |
| **Budget Enforcement** | Per-team daily/monthly spend caps, reject requests when budget exhausted |
| **Cost Attribution** | Per-team, per-model, per-request cost tracking with real-time dashboards |
| **Prometheus Metrics** | requests, latency (p50/p95/p99), tokens, costs, errors, cache hits |
| **Admin API** | Key CRUD, cache control, provider health, circuit reset |

### Prism Cloud (Paid — for teams who need more)

| Feature | Tier |
|---------|------|
| Continuous eval (detect quality regressions on live traffic) | Team ($0.50/1M tokens) |
| Cost optimizer (auto-route to cheapest model meeting quality bar) | Team |
| Dashboard UI (Portkey-quality request explorer + analytics) | Team |
| A/B testing with statistical significance + auto-promote | Enterprise |
| Compliance dashboard + SOC2/HIPAA audit export | Enterprise |
| Multi-cluster federation (single pane across envs) | Enterprise |
| SSO/SCIM + role-based access | Enterprise |
| SLA-backed support + dedicated CSM | Enterprise |

## India-Specific Compliance

Built for RBI-regulated fintechs (Razorpay, Cred, Jupiter, Slice, Vegapay):

```yaml
scanner:
  enabled: true
  rules:
    # Indian Financial PII
    - name: pan_card
      pattern: '[A-Z]{5}[0-9]{4}[A-Z]'
      action: block
      severity: critical

    - name: aadhaar
      pattern: '\b\d{4}\s\d{4}\s\d{4}\b'
      action: block
      severity: critical

    - name: upi_id
      pattern: '[\w.-]+@[\w]+'
      action: redact

    # Global PII
    - name: credit_card
      pattern: '\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b'
      action: block

    - name: phone_india
      pattern: '\+91[\s-]?\d{10}'
      action: redact

    # Prompt injection
    - name: injection
      pattern: 'ignore.*previous.*instructions|you are now|forget.*everything'
      action: block
```

**Data residency:** Self-hosted in your VPC. No data leaves India. Full audit log for RBI inspection.

## API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/chat/completions` | POST | OpenAI-compatible chat (routes to best provider) |
| `/v1/models` | GET | List available models across all providers |
| `/health` | GET | Liveness probe |
| `/ready` | GET | Readiness probe (checks provider connectivity) |
| `/health/providers` | GET | Circuit breaker status per provider |
| `/metrics` | GET | Prometheus metrics (scrape target) |
| `/admin/keys` | POST/GET/DELETE | Manage API keys and budgets |
| `/admin/stats` | GET | Platform statistics |
| `/admin/cache/invalidate` | POST | Clear response cache |
| `/admin/providers/{name}/reset` | POST | Reset circuit breaker |

## Competitive Landscape

| | **Prism** | Kong AI | Portkey | Helicone | LiteLLM | Cloudflare |
|---|---|---|---|---|---|---|
| OpenAI-compatible proxy | ✅ | ✅ | ✅ | ❌ | ✅ | ✅ |
| Self-hosted (your VPC) | ✅ | ✅ | ❌ | ❌ | ✅ | ❌ |
| Multi-provider routing | ✅ | ✅ | ✅ | ❌ | ✅ | ❌ |
| Circuit breakers | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| Semantic caching | ✅ | ❌ | ✅ | ❌ | ❌ | ❌ |
| PII scanning (India) | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Prompt injection blocking | ✅ | Partial | Partial | ❌ | ❌ | ✅ |
| Cost attribution per team | ✅ | ✅ | ✅ | ✅ | Partial | ❌ |
| Continuous production eval | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| A/B testing + auto-promote | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Helm chart (1-command deploy) | ✅ | ✅ | ❌ | ❌ | ✅ | N/A |
| RBI/Indian compliance | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Open source core | ✅ | ❌ | ❌ | ❌ | ✅ | ❌ |
| **Pricing** | **Free + paid** | **$50K+/yr** | **$500+/mo** | **$500+/mo** | **Free** | **Pay/use** |

**TL;DR:** Kong/Cloudflare serve Fortune 500 ($50K+). Portkey/Helicone serve US startups ($500+/mo). Prism serves Indian fintechs (free self-host + $200/mo cloud).

## Architecture

```
Request Flow:
Client → Auth → Scanner → Cache → Router → Provider
                                        ↘ Fallback Provider
         ↕              ↕            ↕
    Rate Limiter   PII Blocker   Circuit Breaker
         ↕              ↕            ↕
    Metrics ←←←←←←←←←←←←←←←←←←←← Cost Tracker
```

Each request passes through:
1. **Auth** — validate API key, check rate limit + budget remaining
2. **Scanner** — block PII (PAN/Aadhaar/CC), detect prompt injection
3. **Cache** — return semantic-matched cached response if available (cost: $0)
4. **Router** — pick provider by priority/weight/latency, skip if circuit open
5. **Execute** — forward to provider with connection pooling + streaming
6. **Record** — log metrics, cost, latency, update circuit breaker state
7. **Fallback** — on failure, try next provider in chain (transparent to client)

## Project Structure

```
prism/
├── proxy/          # FastAPI app, OpenAI-compatible routes
│   └── routes/     # /v1/chat, /v1/models, /health, /admin
├── routing/        # Multi-provider router (priority, weighted, least-latency)
├── resilience/     # Circuit breakers (per-provider failure isolation)
├── cache/          # Response cache (exact + semantic matching)
├── auth/           # API key management, rate limiting, budgets
├── observe/        # Prometheus metrics, OpenTelemetry traces
├── admin/          # Management API
└── config.py       # YAML config loader

prism_cloud/        # Paid features (proprietary)
├── scanner/        # PII detection, prompt injection classifier
├── eval/           # Continuous quality monitoring
├── optimizer/      # Cost optimization engine
└── compliance/     # Audit export, data residency

deploy/
├── helm/prism/     # Production Helm chart (sub-chart deps)
└── k8s/            # Raw K8s manifests

infra/
├── docker-compose.yml   # Full dev stack
└── grafana/             # Pre-built dashboards
```

## Development

```bash
# Clone
git clone https://github.com/kalyanace44/llm-gateway.git
cd llm-gateway

# Install
pip install -e ".[dev]"

# Run tests (62 tests)
pytest tests/ -v

# Start in dev mode
prism serve --dev --port 8000

# Lint
ruff check prism/ tests/
```

## Roadmap

### v0.2 (Current Sprint)
- [x] Semantic caching (embedding-based similarity)
- [x] 10+ provider support (Gemini, Azure, Bedrock, Groq, Together, Fireworks, vLLM, Ollama)
- [ ] Dashboard UI (request explorer, cost analytics, provider health)
- [ ] Production deployment at 2 Indian fintechs

### v0.3
- [ ] Continuous eval engine (quality regression detection)
- [ ] Auto cost optimizer (route to cheapest model meeting quality bar)
- [ ] Webhook alerts (budget exhausted, quality drop, provider down)

### v0.4
- [ ] A/B testing with statistical significance
- [ ] Auto-promote winners, auto-rollback losers
- [ ] Multi-cluster federation

### v1.0 (GA)
- [ ] SOC2 Type II certification
- [ ] SLA-backed enterprise tier
- [ ] 3+ Indian fintech production deployments

## Pricing

| Tier | Price | For |
|------|-------|-----|
| **Open Source** | Free forever | Self-host, unlimited requests, all core features |
| **Team** | $0.50 / 1M tokens | Cloud eval + PII scanning + cost optimizer + dashboard |
| **Enterprise** | Custom | Compliance + federation + SSO + SLA + support |

## License

- Core gateway (`prism/`): **Apache 2.0** — use it however you want
- Cloud features (`prism_cloud/`): Proprietary

---

**Built for Indian fintechs who can't send customer data to US-hosted AI gateways.**

[Documentation](https://kalyanace44.github.io/llm-gateway/) · [Helm Chart](https://kalyanace44.github.io/llm-gateway/charts) · [Issues](https://github.com/kalyanace44/llm-gateway/issues)
