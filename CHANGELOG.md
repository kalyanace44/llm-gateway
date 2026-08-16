# Changelog

All notable changes to Prism are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-08-16

Initial release of Prism — an AI Traffic Control Plane for LLM workloads.

### Added

#### Multi-Provider Routing
- Unified gateway supporting 13+ LLM providers: OpenAI, Anthropic, Gemini, Azure OpenAI, AWS Bedrock, Cohere, Mistral, Together AI, Fireworks, Groq, DeepSeek, Ollama, and vLLM
- Intelligent request routing with failover and load balancing
- Provider-agnostic API with automatic request/response translation

#### Privacy & Compliance
- PII scanning engine detecting 60+ entity types (SSN, credit cards, emails, medical records, etc.)
- Support for 12 compliance frameworks (HIPAA, GDPR, SOC 2, PCI-DSS, CCPA, and more)
- Configurable redaction and blocking policies per route

#### Performance & Reliability
- Semantic caching with configurable similarity thresholds
- Circuit breaker pattern with automatic provider failover
- Rate limiting (per-user, per-key, per-model, global)
- Budget enforcement with spend tracking and alerts

#### Observability
- Real-time dashboard UI for monitoring traffic, latency, and costs
- Structured request/response logging with configurable retention
- Prometheus metrics export

#### Deployment
- Helm chart with sub-chart dependencies (Redis, Prometheus, Grafana)
- Published to PyPI (`pip install prism-gateway`)
- Container images published to GHCR (`ghcr.io/kalyanace44/llm-gateway`)
- Support for Python 3.11+

[0.1.0]: https://github.com/kalyanace44/llm-gateway/releases/tag/v0.1.0
