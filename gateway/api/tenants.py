"""Tenant management API."""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.db.session import get_session
from gateway.services.tenant import TenantService

router = APIRouter(prefix="/v1/tenants", tags=["tenants"])


# --- Request/Response models ---

class CreateTenantRequest(BaseModel):
    name: str
    slug: str = Field(..., pattern=r"^[a-z0-9\-]+$", max_length=64)
    tier: str = "free"
    config: dict | None = None


class CreateAPIKeyRequest(BaseModel):
    name: str
    key: str = Field(..., min_length=8)
    rate_limit_rpm: int | None = None
    rate_limit_tpm: int | None = None
    allowed_models: list[str] | None = None
    budget_usd: float | None = None


class TenantResponse(BaseModel):
    id: str
    name: str
    slug: str
    tier: str
    config: dict
    is_active: bool
    created_at: str


# --- Admin check ---

def _check_admin(request: Request):
    from gateway.config import settings
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing auth")
    token = auth[7:]
    if token != settings.admin_key:
        raise HTTPException(status_code=403, detail="Invalid admin key")


# --- Endpoints ---

@router.post("")
async def create_tenant(
    body: CreateTenantRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Create a new tenant."""
    _check_admin(request)
    svc = TenantService(session)

    # Check slug uniqueness
    existing = await svc.get_tenant_by_slug(body.slug)
    if existing:
        raise HTTPException(status_code=409, detail=f"Slug '{body.slug}' already exists")

    tenant = await svc.create_tenant(
        name=body.name,
        slug=body.slug,
        tier=body.tier,
        config=body.config,
    )
    return {
        "id": str(tenant.id),
        "name": tenant.name,
        "slug": tenant.slug,
        "tier": tenant.tier,
        "config": tenant.config,
        "created_at": tenant.created_at.isoformat() if tenant.created_at else None,
    }


@router.get("")
async def list_tenants(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """List all tenants."""
    _check_admin(request)
    svc = TenantService(session)
    tenants = await svc.list_tenants()
    return {
        "tenants": [
            {
                "id": str(t.id),
                "name": t.name,
                "slug": t.slug,
                "tier": t.tier,
                "is_active": t.is_active,
            }
            for t in tenants
        ]
    }


@router.get("/{slug}")
async def get_tenant(
    slug: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Get tenant by slug."""
    _check_admin(request)
    svc = TenantService(session)
    tenant = await svc.get_tenant_by_slug(slug)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return {
        "id": str(tenant.id),
        "name": tenant.name,
        "slug": tenant.slug,
        "tier": tenant.tier,
        "config": tenant.config,
        "is_active": tenant.is_active,
        "created_at": tenant.created_at.isoformat() if tenant.created_at else None,
    }


@router.post("/{slug}/keys")
async def create_tenant_key(
    slug: str,
    body: CreateAPIKeyRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Create an API key for a tenant."""
    _check_admin(request)
    svc = TenantService(session)
    tenant = await svc.get_tenant_by_slug(slug)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    key = await svc.create_api_key(
        tenant_id=tenant.id,
        name=body.name,
        raw_key=body.key,
        rate_limit_rpm=body.rate_limit_rpm,
        rate_limit_tpm=body.rate_limit_tpm,
        allowed_models=body.allowed_models,
        budget_usd=body.budget_usd,
    )
    return {
        "id": str(key.id),
        "tenant_id": str(key.tenant_id),
        "name": key.name,
        "created_at": key.created_at.isoformat() if key.created_at else None,
    }
