# Prism — AI Traffic Control Plane

> Cloudflare for LLM traffic. Route, protect, optimize, observe.

Every AI call flows through Prism. Drop-in OpenAI SDK-compatible proxy that gives you routing, caching, security scanning, cost governance, continuous eval, and compliance logging — without changing application code.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          Your Application Code                               │
│                      (OpenAI SDK, LangChain, etc.)                           │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │  ← just change base_url
┌──────────────────────────────────▼──────────────────────────────────────────┐
│                              PRISM GATEWAY                                    │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────────┐  │
│  │  Auth +  │ │ Security │ │  Smart   │ │  Cache   │ │  Observability   │  │
│  │  Limits  │ │  Scanner │ │  Router  │ │  Layer   │ │  + Cost Track    │  │
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
   │ OpenAI  │ │Anthropic│ │  Google  │ │  vLLM /  │ │  Any OpenAI  │
   │  GPT-4  │ │ Claude  │ │  Gemini  │ │  Local   │ │  Compatible  │
   └─────────┘ └─────────┘ └──────────┘ └──────────┘ └──────────────┘
```

## Why Prism

| Problem | How Prism Solves It |
|---------|-------------------|
| **Cost explosion** — $500K-$5M/mo with zero attribution | Per-team cost tracking, budget enforcement, automatic cheaper-model substitution |
| **Model sprawl** — 15 models, 5 providers, no governance | Centralized registry, consistent failover, quality-gated promotion |
| **Reliability** — 2-5% API failure rate, naive retries | Circuit breakers, intelligent cross-provider fallback, automatic recovery |
| **Compliance** — SOC2/HIPAA requires full audit trails | Every request logged with full lineage, data residency controls, export |
| **Security** — PII leakage, prompt injection | Inline scanning before requests leave your network |
| **Eval gap** — offline evals miss production regressions | Continuous quality monitoring on real traffic with statistical significance |

## Quickstart

```bash
pip install prism-gateway

# Start with a single provider
export PRISM_PROVIDERS='[{"name": "openai", "api_key": "sk-...", "models": ["gpt-4o"]}]'
prism serve --port 8000

# Your app just changes base_url
from openai import OpenAI
client = OpenAI(base_url="http://localhost:8000/v1", api_key="your-prism-key")
```

## Architecture

### OSS Core (Apache 2.0)

| Module | What it does |
|--------|-------------|
| `prism.proxy` | OpenAI-compatible API (chat, completions, embeddings) |
| `prism.routing` | Multi-provider routing, weighted load balancing, fallback chains |
| `prism.resilience` | Circuit breakers, retry with backoff, health monitoring |
| `prism.cache` | Semantic + exact-match response caching |
| `prism.auth` | API key management, per-team rate limiting, budget caps |
| `prism.observe` | Prometheus metrics, OpenTelemetry traces, structured logging |
| `prism.admin` | Management API for keys, providers, routing rules |

### Prism Cloud (Paid)

| Feature | Tier |
|---------|------|
| Continuous eval (quality regression detection) | Team |
| PII/prompt injection scanning | Team |
| Cost optimization recommendations | Team |
| Compliance dashboard + audit export | Enterprise |
| Multi-cluster federation | Enterprise |
| Advanced A/B testing with auto-promote | Enterprise |
| SSO/SCIM + role-based access | Enterprise |
| SLA-backed support | Enterprise |

## Deployment (Kubernetes)

```bash
# Production deployment with HPA
kubectl apply -f deploy/k8s/

# Scales 2→50 pods based on request rate
# Zero-downtime rolling updates
# PodDisruptionBudget prevents outages
```

## Pricing

| Tier | Price | Includes |
|------|-------|----------|
| **Open Source** | Free forever | Self-host, unlimited requests, all core features |
| **Team** | $0.50 / 1M tokens | Cloud eval + security scanning + cost insights |
| **Enterprise** | Custom | Compliance + federation + support + SLA |

## Competitive Landscape

| | Prism | Portkey | Helicone | LiteLLM |
|---|---|---|---|---|
| OpenAI-compatible proxy | ✅ | ✅ | ❌ | ✅ |
| Multi-provider routing | ✅ | ✅ | ❌ | ✅ |
| Circuit breakers + self-healing | ✅ | ❌ | ❌ | ❌ |
| Continuous production eval | ✅ | ❌ | ❌ | ❌ |
| PII/injection scanning | ✅ | Partial | ❌ | ❌ |
| Cost optimization engine | ✅ | ❌ | Partial | ❌ |
| Compliance (SOC2/HIPAA export) | ✅ | ❌ | ❌ | ❌ |
| Open source core | ✅ | ❌ | ❌ | ✅ |
| A/B testing with significance | ✅ | ❌ | ❌ | ❌ |
| Semantic caching | ✅ | ✅ | ❌ | ❌ |

## License

- Core gateway: Apache 2.0
- Cloud features: Proprietary (prism_cloud/)
