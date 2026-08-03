"""LLM Gateway configuration."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


class BackendConfig(BaseModel):
    """Configuration for a single LLM backend."""

    name: str
    provider: str  # vllm | openai | anthropic | ollama | custom
    base_url: str
    api_key: str = ""
    models: list[str] = Field(default_factory=list)
    max_concurrent: int = 100
    timeout: float = 120.0
    enabled: bool = True
    weight: float = 1.0  # For A/B routing
    priority: int = 0  # Higher = preferred in fallback chains


class RateLimitConfig(BaseModel):
    """Rate limiting configuration."""

    requests_per_minute: int = 60
    tokens_per_minute: int = 100_000
    burst_multiplier: float = 1.5


class CostConfig(BaseModel):
    """Cost tracking configuration per model."""

    model: str
    input_cost_per_1k: float = 0.0
    output_cost_per_1k: float = 0.0


class Settings(BaseSettings):
    """Application settings from environment variables."""

    host: str = "0.0.0.0"
    port: int = 8080
    workers: int = 1
    debug: bool = False

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # PostgreSQL (for usage tracking)
    database_url: str = "sqlite:///./gateway.db"

    # Config file path
    config_path: str = "config.yaml"

    # Admin
    admin_key: str = "admin-secret"

    class Config:
        env_prefix = "GATEWAY_"


class GatewayConfig(BaseModel):
    """Full gateway configuration loaded from YAML."""

    backends: list[BackendConfig] = Field(default_factory=list)
    rate_limits: RateLimitConfig = Field(default_factory=RateLimitConfig)
    costs: list[CostConfig] = Field(default_factory=list)
    default_model: str = ""
    fallback_chain: list[str] = Field(default_factory=list)
    cache_enabled: bool = True
    cache_ttl: int = 3600

    @classmethod
    def from_yaml(cls, path: str | Path) -> "GatewayConfig":
        """Load config from YAML file."""
        path = Path(path)
        if not path.exists():
            return cls()
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return cls(**data)


# Global singletons
settings = Settings()
config = GatewayConfig.from_yaml(settings.config_path)
