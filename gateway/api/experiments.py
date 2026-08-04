"""Experiment tracking API — A/B tests, variant assignment, results."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.db.session import get_session
from gateway.services.experiments import ExperimentService

router = APIRouter(prefix="/v1/experiments", tags=["experiments"])


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

class CreateExperimentRequest(BaseModel):
    name: str
    description: str | None = None
    tenant_ids: list[str] | None = None
    config: dict = Field(..., description="Must include 'variants' list with name/model/weight")


class RecordEventRequest(BaseModel):
    variant: str
    metrics: dict
    tenant_id: str | None = None
    request_id: str | None = None


# --- Endpoints ---

@router.post("")
async def create_experiment(
    body: CreateExperimentRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Create a new experiment."""
    _check_admin(request)

    # Validate config has variants
    if "variants" not in body.config or not body.config["variants"]:
        raise HTTPException(status_code=400, detail="config must include 'variants' list")

    svc = ExperimentService(session)
    exp = await svc.create_experiment(
        name=body.name,
        config=body.config,
        description=body.description,
        tenant_ids=body.tenant_ids,
    )
    return _exp_response(exp)


@router.get("")
async def list_experiments(
    request: Request,
    status: str | None = None,
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    """List experiments."""
    _check_admin(request)
    svc = ExperimentService(session)
    exps = await svc.list_experiments(status=status, limit=limit)
    return {"experiments": [_exp_response(e) for e in exps]}


@router.get("/{experiment_id}")
async def get_experiment(
    experiment_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Get experiment details."""
    _check_admin(request)
    svc = ExperimentService(session)
    exp = await svc.get_experiment(experiment_id)
    if not exp:
        raise HTTPException(status_code=404, detail="Experiment not found")
    return _exp_response(exp)


@router.post("/{experiment_id}/start")
async def start_experiment(
    experiment_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Start an experiment (draft → running)."""
    _check_admin(request)
    svc = ExperimentService(session)
    try:
        exp = await svc.start_experiment(experiment_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _exp_response(exp)


@router.post("/{experiment_id}/stop")
async def stop_experiment(
    experiment_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Stop an experiment and compute results."""
    _check_admin(request)
    svc = ExperimentService(session)
    try:
        exp = await svc.stop_experiment(experiment_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _exp_response(exp)


@router.post("/{experiment_id}/cancel")
async def cancel_experiment(
    experiment_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Cancel an experiment."""
    _check_admin(request)
    svc = ExperimentService(session)
    try:
        exp = await svc.cancel_experiment(experiment_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _exp_response(exp)


@router.post("/{experiment_id}/events")
async def record_event(
    experiment_id: str,
    body: RecordEventRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Record an observation event for an experiment variant."""
    svc = ExperimentService(session)
    exp = await svc.get_experiment(experiment_id)
    if not exp:
        raise HTTPException(status_code=404, detail="Experiment not found")
    if exp.status != "running":
        raise HTTPException(status_code=400, detail="Experiment is not running")

    event = await svc.record_event(
        experiment_id=experiment_id,
        variant=body.variant,
        metrics=body.metrics,
        tenant_id=body.tenant_id,
        request_id=body.request_id,
    )
    return {
        "id": event.id,
        "experiment_id": event.experiment_id,
        "variant": event.variant,
        "metrics": event.metrics,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


@router.get("/{experiment_id}/results")
async def get_results(
    experiment_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Get experiment results with statistical analysis."""
    _check_admin(request)
    svc = ExperimentService(session)
    exp = await svc.get_experiment(experiment_id)
    if not exp:
        raise HTTPException(status_code=404, detail="Experiment not found")
    results = await svc.get_results(experiment_id)
    return results


# --- Response helper ---

def _exp_response(exp) -> dict:
    return {
        "id": exp.id,
        "name": exp.name,
        "description": exp.description,
        "tenant_ids": exp.tenant_ids,
        "config": exp.config,
        "status": exp.status,
        "sample_count": exp.sample_count,
        "results": exp.results,
        "created_at": exp.created_at.isoformat() if exp.created_at else None,
        "started_at": exp.started_at.isoformat() if exp.started_at else None,
        "ended_at": exp.ended_at.isoformat() if exp.ended_at else None,
    }
