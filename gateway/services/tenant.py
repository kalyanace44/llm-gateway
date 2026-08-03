"""Tenant management service."""
from __future__ import annotations

import hashlib
import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.db.models import Tenant, TenantAPIKey


class TenantService:
    """CRUD operations for tenants."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_tenant(
        self,
        name: str,
        slug: str,
        tier: str = "free",
        config: dict | None = None,
    ) -> Tenant:
        """Create a new tenant."""
        tenant = Tenant(
            name=name,
            slug=slug,
            tier=tier,
            config=config or self._default_config(tier),
        )
        self.session.add(tenant)
        await self.session.commit()
        await self.session.refresh(tenant)
        return tenant

    async def get_tenant(self, tenant_id: str) -> Optional[Tenant]:
        """Get tenant by ID."""
        result = await self.session.execute(
            select(Tenant).where(Tenant.id == tenant_id)
        )
        return result.scalar_one_or_none()

    async def get_tenant_by_slug(self, slug: str) -> Optional[Tenant]:
        """Get tenant by slug."""
        result = await self.session.execute(
            select(Tenant).where(Tenant.slug == slug)
        )
        return result.scalar_one_or_none()

    async def get_tenant_by_api_key(self, api_key: str) -> Optional[tuple[Tenant, TenantAPIKey]]:
        """Resolve tenant from API key."""
        key_hash = hashlib.sha256(api_key.encode()).hexdigest()
        result = await self.session.execute(
            select(TenantAPIKey)
            .where(TenantAPIKey.key_hash == key_hash, TenantAPIKey.is_active == True)
        )
        tenant_key = result.scalar_one_or_none()
        if not tenant_key:
            return None

        tenant = await self.get_tenant(tenant_key.tenant_id)
        if not tenant or not tenant.is_active:
            return None

        return tenant, tenant_key

    async def list_tenants(self, active_only: bool = True) -> list[Tenant]:
        """List all tenants."""
        query = select(Tenant)
        if active_only:
            query = query.where(Tenant.is_active == True)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def create_api_key(
        self,
        tenant_id: uuid.UUID,
        name: str,
        raw_key: str,
        rate_limit_rpm: int | None = None,
        rate_limit_tpm: int | None = None,
        allowed_models: list[str] | None = None,
        budget_usd: float | None = None,
    ) -> TenantAPIKey:
        """Create an API key for a tenant."""
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        api_key = TenantAPIKey(
            tenant_id=tenant_id,
            key_hash=key_hash,
            name=name,
            rate_limit_rpm=rate_limit_rpm,
            rate_limit_tpm=rate_limit_tpm,
            allowed_models=allowed_models or [],
            budget_usd=budget_usd,
        )
        self.session.add(api_key)
        await self.session.commit()
        await self.session.refresh(api_key)
        return api_key

    async def revoke_api_key(self, key_hash: str) -> bool:
        """Revoke an API key."""
        result = await self.session.execute(
            select(TenantAPIKey).where(TenantAPIKey.key_hash == key_hash)
        )
        key = result.scalar_one_or_none()
        if key:
            key.is_active = False
            await self.session.commit()
            return True
        return False

    @staticmethod
    def _default_config(tier: str) -> dict:
        """Default tenant config by tier."""
        configs = {
            "free": {
                "limits": {
                    "requests_per_minute": 30,
                    "tokens_per_minute": 50000,
                    "monthly_budget_usd": 10.0,
                },
                "memory": {"enabled": True, "auto_summarize": True, "retention_days": 30},
            },
            "pro": {
                "limits": {
                    "requests_per_minute": 120,
                    "tokens_per_minute": 500000,
                    "monthly_budget_usd": 200.0,
                },
                "memory": {"enabled": True, "auto_summarize": True, "retention_days": 90},
            },
            "enterprise": {
                "limits": {
                    "requests_per_minute": 1000,
                    "tokens_per_minute": 5000000,
                    "monthly_budget_usd": 5000.0,
                },
                "memory": {"enabled": True, "auto_summarize": True, "retention_days": 365},
            },
        }
        return configs.get(tier, configs["free"])
