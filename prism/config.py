"""Prism configuration — providers, routing, limits."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import yaml


@dataclass
class ProviderConfig:
    """A single LLM provider backend."""
    name: str
    base_url: str
    api_key: str
    models: list[str] = field(default_factory=list)
    weight: float = 1.0
    max_retries: int = 2
    timeout: float = 60.0
    priority: int = 0  # lower = preferred


@dataclass
class RateLimitConfig:
    """Rate limiting configuration."""
    requests_per_minute: int = 600
    tokens_per_minute: int = 1_000_000
    budget_usd_per_day: float = 0.0  # 0 = unlimited


@dataclass
class CacheConfig:
    """Response cache settings."""
    enabled: bool = True
    ttl_seconds: int = 3600
    max_entries: int = 50_000
    semantic_threshold: float = 0.95  # cosine similarity for semantic cache


@dataclass
class ResilienceConfig:
    """Circuit breaker and retry settings."""
    failure_threshold: int = 5
    recovery_timeout_seconds: float = 60.0
    success_threshold: int = 3
    retry_on_status: list[int] = field(default_factory=lambda: [429, 500, 502, 503, 529])


@dataclass
class ObserveConfig:
    """Observability settings."""
    metrics_enabled: bool = True
    traces_enabled: bool = True
    log_level: str = "info"
    log_requests: bool = True
    log_responses: bool = False  # careful with PII


@dataclass
class PrismConfig:
    """Top-level Prism Gateway configuration."""
    host: str = "0.0.0.0"
    port: int = 8000
    workers: int = 4
    admin_key: str = ""
    providers: list[ProviderConfig] = field(default_factory=list)
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    resilience: ResilienceConfig = field(default_factory=ResilienceConfig)
    observe: ObserveConfig = field(default_factory=ObserveConfig)

    @classmethod
    def from_env(cls) -> PrismConfig:
        """Load config from environment or config file."""
        config_path = os.environ.get("PRISM_CONFIG", "prism.yaml")
        if os.path.exists(config_path):
            return cls.from_yaml(config_path)
        return cls._from_env_vars()

    @classmethod
    def from_yaml(cls, path: str) -> PrismConfig:
        """Load from YAML config file."""
        with open(path) as f:
            raw = yaml.safe_load(f)

        providers = [
            ProviderConfig(**p) for p in raw.get("providers", [])
        ]
        cfg = cls(
            host=raw.get("host", "0.0.0.0"),
            port=raw.get("port", 8000),
            workers=raw.get("workers", 4),
            admin_key=raw.get("admin_key", os.environ.get("PRISM_ADMIN_KEY", "")),
            providers=providers,
        )
        if "rate_limit" in raw:
            cfg.rate_limit = RateLimitConfig(**raw["rate_limit"])
        if "cache" in raw:
            cfg.cache = CacheConfig(**raw["cache"])
        if "resilience" in raw:
            cfg.resilience = ResilienceConfig(**raw["resilience"])
        if "observe" in raw:
            cfg.observe = ObserveConfig(**raw["observe"])
        return cfg

    @classmethod
    def _from_env_vars(cls) -> PrismConfig:
        """Minimal config from env vars."""
        import json
        providers_raw = os.environ.get("PRISM_PROVIDERS", "[]")
        providers = [ProviderConfig(**p) for p in json.loads(providers_raw)]
        return cls(
            port=int(os.environ.get("PRISM_PORT", "8000")),
            workers=int(os.environ.get("PRISM_WORKERS", "4")),
            admin_key=os.environ.get("PRISM_ADMIN_KEY", ""),
            providers=providers,
        )

    def get_provider(self, name: str) -> ProviderConfig | None:
        """Get provider by name."""
        for p in self.providers:
            if p.name == name:
                return p
        return None

    def get_providers_for_model(self, model: str) -> list[ProviderConfig]:
        """Get all providers that serve a given model, sorted by priority."""
        matches = [p for p in self.providers if model in p.models or not p.models]
        return sorted(matches, key=lambda p: p.priority)
