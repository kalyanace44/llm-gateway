"""API key authentication and authorization."""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class APIKey:
    """Represents an API key with metadata."""

    key_hash: str
    name: str
    created_at: float = field(default_factory=time.time)
    enabled: bool = True
    rate_limit_rpm: Optional[int] = None  # Override global rate limit
    rate_limit_tpm: Optional[int] = None
    budget_usd: Optional[float] = None  # Monthly budget cap
    spent_usd: float = 0.0
    allowed_models: list[str] = field(default_factory=list)  # Empty = all
    metadata: dict = field(default_factory=dict)


class AuthManager:
    """Manages API key authentication."""

    def __init__(self):
        self._keys: dict[str, APIKey] = {}  # key_hash -> APIKey

    @staticmethod
    def hash_key(key: str) -> str:
        """Hash an API key for storage."""
        return hashlib.sha256(key.encode()).hexdigest()

    def register_key(self, raw_key: str, name: str, **kwargs) -> APIKey:
        """Register a new API key."""
        key_hash = self.hash_key(raw_key)
        api_key = APIKey(key_hash=key_hash, name=name, **kwargs)
        self._keys[key_hash] = api_key
        return api_key

    def validate(self, raw_key: str) -> Optional[APIKey]:
        """
        Validate an API key.
        Returns the APIKey if valid, None if invalid.
        """
        if not raw_key:
            return None
        key_hash = self.hash_key(raw_key)
        api_key = self._keys.get(key_hash)
        if api_key is None:
            return None
        if not api_key.enabled:
            return None
        if api_key.budget_usd and api_key.spent_usd >= api_key.budget_usd:
            return None
        return api_key

    def check_model_access(self, api_key: APIKey, model: str) -> bool:
        """Check if key has access to a specific model."""
        if not api_key.allowed_models:
            return True  # Empty = unrestricted
        return model in api_key.allowed_models

    def record_usage(self, api_key: APIKey, cost_usd: float):
        """Record spend against a key."""
        api_key.spent_usd += cost_usd

    def list_keys(self) -> list[APIKey]:
        """List all registered keys."""
        return list(self._keys.values())

    def revoke_key(self, raw_key: str) -> bool:
        """Disable a key."""
        key_hash = self.hash_key(raw_key)
        if key_hash in self._keys:
            self._keys[key_hash].enabled = False
            return True
        return False
