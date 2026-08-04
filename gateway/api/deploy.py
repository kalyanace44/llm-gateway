"""Deployment pipeline API — model registry, deployments, canary, rollback."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.db.session import get_session
from gateway.services.deploy import ModelRegistryService, DeploymentService

router = APIRouter(prefix="/v1/deployments", tags=["deployments"])


# --- Admin check ---

def _check_admin(request: Request):
    from gateway.config import settings
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing auth")
    token = auth[7:]
    if token != settings.admin_key:
        raise HTTPException(status_code=403, detail="Invalid admin key")


# --- Request models ---

class RegisterModelRequest(BaseModel):
    name: str
    version: str
    artifact_uri: str | None = None
    serving_config: dict | None = None
    quality_scores: dict | None = None
    metadata: dict | None = None


class CreateDeploymentRequest(BaseModel):
    model_id: str
    strategy: str = Field("canary", pattern=r"^(canary|blue-green|shadow)$")
    traffic_pct: float = Field(0.05, ge=0.0, le=1.0)
    config: dict | None = None


class AdvanceDeploymentRequest(BaseModel):
    metrics: dict | None = None


class UpdateQualityRequest(BaseModel):
    scores: dict


# --- Model Registry Endpoints ---

@router.post("/models/register")
async def register_model(
    body: RegisterModelRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Register a new model version."""
    _check_admin(request)
    svc = ModelRegistryService(session)

    # Check if already exists
    existing = await svc.get_model_by_name_version(body.name, body.version)
    if existing:
        raise HTTPException(status_code=409, detail=f"Model {body.name}:{body.version} already registered")

    model = await svc.register_model(
        name=body.name,
        version=body.version,
        artifact_uri=body.artifact_uri,
        serving_config=body.serving_config,
        quality_scores=body.quality_scores,
        metadata=body.metadata,
    )
    return _model_response(model)


@router.get("/models/registry")
async def list_models(
    request: Request,
    name: str | None = None,
    status: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    """List registered models."""
    _check_admin(request)
    svc = ModelRegistryService(session)
    models = await svc.list_models(name=name, status=status)
    return {"models": [_model_response(m) for m in models]}


@router.get("/models/{model_id}")
async def get_model(
    model_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Get model version details."""
    _check_admin(request)
    svc = ModelRegistryService(session)
    model = await svc.get_model(model_id)
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")
    return _model_response(model)


@router.post("/models/{model_id}/promote")
async def promote_model(
    model_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Promote a model to production."""
    _check_admin(request)
    svc = ModelRegistryService(session)
    try:
        model = await svc.promote_model(model_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return _model_response(model)


@router.post("/models/{model_id}/retire")
async def retire_model(
    model_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Retire a model version."""
    _check_admin(request)
    svc = ModelRegistryService(session)
    try:
        model = await svc.retire_model(model_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return _model_response(model)


@router.post("/models/{model_id}/quality")
async def update_quality(
    model_id: str,
    body: UpdateQualityRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Update quality scores for a model."""
    _check_admin(request)
    svc = ModelRegistryService(session)
    try:
        model = await svc.update_quality_scores(model_id, body.scores)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return _model_response(model)


# --- Deployment Endpoints ---

@router.post("")
async def create_deployment(
    body: CreateDeploymentRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Trigger a new deployment (canary/blue-green/shadow)."""
    _check_admin(request)
    svc = DeploymentService(session)
    try:
        deploy = await svc.create_deployment(
            model_id=body.model_id,
            strategy=body.strategy,
            traffic_pct=body.traffic_pct,
            config=body.config,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return _deploy_response(deploy)


@router.get("")
async def list_deployments(
    request: Request,
    status: str | None = None,
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    """List deployments."""
    _check_admin(request)
    svc = DeploymentService(session)
    deploys = await svc.list_deployments(status=status, limit=limit)
    return {"deployments": [_deploy_response(d) for d in deploys]}


@router.get("/{deployment_id}")
async def get_deployment(
    deployment_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Get deployment details."""
    _check_admin(request)
    svc = DeploymentService(session)
    deploy = await svc.get_deployment(deployment_id)
    if not deploy:
        raise HTTPException(status_code=404, detail="Deployment not found")
    return _deploy_response(deploy)


@router.post("/{deployment_id}/advance")
async def advance_deployment(
    deployment_id: str,
    body: AdvanceDeploymentRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Advance deployment to next stage."""
    _check_admin(request)
    svc = DeploymentService(session)
    try:
        deploy = await svc.advance_deployment(deployment_id, metrics=body.metrics)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return _deploy_response(deploy)


@router.post("/{deployment_id}/rollback")
async def rollback_deployment(
    deployment_id: str,
    request: Request,
    reason: str = "",
    session: AsyncSession = Depends(get_session),
):
    """Force rollback a deployment."""
    _check_admin(request)
    svc = DeploymentService(session)
    try:
        deploy = await svc.rollback_deployment(deployment_id, reason=reason)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return _deploy_response(deploy)


@router.get("/{deployment_id}/events")
async def deployment_events(
    deployment_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Get deployment event log."""
    _check_admin(request)
    svc = DeploymentService(session)
    events = await svc.get_deployment_events(deployment_id)
    return {
        "events": [
            {
                "id": e.id,
                "event_type": e.event_type,
                "detail": e.detail,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in events
        ]
    }


# --- Response helpers ---

def _model_response(model: "ModelVersion") -> dict:
    return {
        "id": model.id,
        "name": model.name,
        "version": model.version,
        "artifact_uri": model.artifact_uri,
        "serving_config": model.serving_config,
        "quality_scores": model.quality_scores,
        "status": model.status,
        "created_at": model.created_at.isoformat() if model.created_at else None,
        "promoted_at": model.promoted_at.isoformat() if model.promoted_at else None,
    }


def _deploy_response(deploy: "Deployment") -> dict:
    return {
        "id": deploy.id,
        "model_id": deploy.model_id,
        "strategy": deploy.strategy,
        "traffic_pct": deploy.traffic_pct,
        "status": deploy.status,
        "config": deploy.config,
        "metrics": deploy.metrics,
        "error_message": deploy.error_message,
        "started_at": deploy.started_at.isoformat() if deploy.started_at else None,
        "completed_at": deploy.completed_at.isoformat() if deploy.completed_at else None,
    }
