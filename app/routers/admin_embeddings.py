"""
Admin embedding-management router.

Endpoints:
  GET  /api/settings/embeddings/catalog    — supported models + active spec
  GET  /api/settings/embeddings/status     — counts and current job
  POST /api/settings/embeddings/switch     — start re-embed migration to a new model
  GET  /api/settings/embeddings/jobs/{id}  — poll job progress
  POST /api/settings/embeddings/jobs/{id}/cancel
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.embedding_catalog import (
    EmbeddingModelSpec,
    UnknownEmbeddingModel,
    get_spec,
    list_specs,
)
from app.database import get_db
from app.database.models import (
    EmbeddingJob,
    Employee,
    WikiPage,
    get_embedding_model_for_dim,
)
from app.services.audit_service import log_audit
from app.services.auth_service import require_permission

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class EmbeddingSpecOut(BaseModel):
    id: str
    provider: str
    model_id: str
    dimension: int
    label: str
    cost_per_1m_tokens: Optional[float]
    notes: Optional[str]
    api_key_configured: bool


class EmbeddingCatalogOut(BaseModel):
    active_spec_id: Optional[str]
    specs: list[EmbeddingSpecOut]


class EmbeddingJobOut(BaseModel):
    id: uuid.UUID
    model_spec_id: str
    status: str
    total_pages: int
    done_pages: int
    error_message: Optional[str]
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    created_at: datetime


class EmbeddingStatusOut(BaseModel):
    active_spec_id: Optional[str]
    total_pages: int
    embedded_pages: int  # rows in active spec's table
    current_job: Optional[EmbeddingJobOut]


class EmbeddingSwitchBody(BaseModel):
    model_spec_id: str
    # Required when model_spec_id is not in the catalog (custom model)
    dimension: Optional[int] = None
    model_id: Optional[str] = None   # raw API model ID (may differ from spec_id path)


class EmbeddingSwitchOut(BaseModel):
    job_id: uuid.UUID


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _spec_to_out(
    spec: EmbeddingModelSpec, db: AsyncSession
) -> EmbeddingSpecOut:
    from app.services.config_service import ConfigService, embedding_api_key_for

    svc = ConfigService(db)
    key = await svc.get(embedding_api_key_for(spec.provider))
    return EmbeddingSpecOut(
        id=spec.id,
        provider=spec.provider,
        model_id=spec.model_id,
        dimension=spec.dimension,
        label=spec.label,
        cost_per_1m_tokens=spec.cost_per_1m_tokens,
        notes=spec.notes,
        api_key_configured=bool(key),
    )


def _job_to_out(job: EmbeddingJob) -> EmbeddingJobOut:
    return EmbeddingJobOut(
        id=job.id,
        model_spec_id=job.model_spec_id,
        status=job.status,
        total_pages=job.total_pages,
        done_pages=job.done_pages,
        error_message=job.error_message,
        started_at=job.started_at,
        finished_at=job.finished_at,
        created_at=job.created_at,
    )


async def _get_current_job(db: AsyncSession) -> Optional[EmbeddingJob]:
    row = (
        await db.execute(
            select(EmbeddingJob)
            .where(EmbeddingJob.status.in_(["pending", "running"]))
            .order_by(EmbeddingJob.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return row


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/settings/embeddings/catalog", response_model=EmbeddingCatalogOut)
async def get_catalog(
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("org:settings:manage"),
):
    from app.ai.registry import ProviderRegistry

    registry = ProviderRegistry(db)
    active = await registry.get_active_embedding_spec_id()
    specs = [await _spec_to_out(s, db) for s in list_specs()]
    return EmbeddingCatalogOut(active_spec_id=active, specs=specs)


@router.get("/settings/embeddings/status", response_model=EmbeddingStatusOut)
async def get_status(
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("org:settings:manage"),
):
    from app.ai.registry import ProviderRegistry

    registry = ProviderRegistry(db)
    active = await registry.get_active_embedding_spec_id()

    total_pages = (
        await db.execute(
            select(func.count(WikiPage.id)).where(
                WikiPage.slug.notin_(["_index", "_log"])
            )
        )
    ).scalar_one()

    embedded = 0
    if active:
        spec = get_spec(active)
        Emb = get_embedding_model_for_dim(spec.dimension)
        embedded = (
            await db.execute(
                select(func.count(Emb.page_id)).where(
                    Emb.model_spec_id == active
                )
            )
        ).scalar_one()

    job = await _get_current_job(db)
    return EmbeddingStatusOut(
        active_spec_id=active,
        total_pages=int(total_pages),
        embedded_pages=int(embedded),
        current_job=_job_to_out(job) if job else None,
    )


@router.post("/settings/embeddings/switch", response_model=EmbeddingSwitchOut)
async def switch_embedding_model(
    body: EmbeddingSwitchBody,
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("org:settings:manage"),
):
    from app.services.config_service import ConfigService, embedding_api_key_for

    try:
        spec = get_spec(body.model_spec_id)
    except UnknownEmbeddingModel:
        if body.dimension is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Model '{body.model_spec_id}' is not in the catalog. "
                    "Provide 'dimension' to use a custom model."
                ),
            )
        supported = (768, 1536, 3072)
        if body.dimension not in supported:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported dimension {body.dimension}. Choose from {supported}.",
            )
        provider = body.model_spec_id.split("/")[0]
        raw_model_id = body.model_id or "/".join(body.model_spec_id.split("/")[1:])
        spec = EmbeddingModelSpec(
            id=body.model_spec_id,
            provider=provider,
            model_id=raw_model_id,
            dimension=body.dimension,
            max_input_tokens=8191,
            label=f"Custom — {raw_model_id} ({body.dimension}d)",
            cost_per_1m_tokens=None,
        )
        # Persist custom spec info so the registry can reconstruct it later
        from app.services.config_service import ConfigService
        csvc = ConfigService(db)
        await csvc.set("embedding_custom_spec_id", body.model_spec_id)
        await csvc.set("embedding_custom_model_id", raw_model_id)
        await csvc.set("embedding_custom_dimension", str(body.dimension))
        await csvc.set("embedding_custom_provider", provider)

    # Refuse if a job is already in flight — frontend should cancel first.
    in_flight = await _get_current_job(db)
    if in_flight is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Another embedding job is already {in_flight.status}. "
                "Cancel it before starting a new switch."
            ),
        )

    # Make sure the chosen provider has an API key configured.
    svc = ConfigService(db)
    api_key = await svc.get(embedding_api_key_for(spec.provider))
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail=(
                f"No API key configured for provider '{spec.provider}'. "
                f"Save the API key first, then switch."
            ),
        )

    job = EmbeddingJob(model_spec_id=spec.id, status="pending")
    db.add(job)
    await db.flush()
    job_id = job.id

    await log_audit(
        db,
        _user,
        "switch_embedding_model",
        "settings",
        "global",
        reason=f"Switching active embedding model to {spec.id}",
    )
    await db.commit()

    # Enqueue arq job.
    try:
        from app.worker import get_arq_pool

        pool = await get_arq_pool()
        await pool.enqueue_job("reembed_all_pages_task", str(job_id))
    except Exception as exc:
        # Mark job as failed so the UI doesn't poll forever. Neither the stored
        # error_message nor the response carries `str(exc)`: an arq connection failure
        # renders as the Redis host, port and password-bearing DSN, and EmbeddingJobOut
        # returns error_message straight to the client, so the leak had two exits.
        async with db.begin():
            await db.execute(
                update(EmbeddingJob)
                .where(EmbeddingJob.id == job_id)
                .values(
                    status="failed",
                    error_message="Enqueue failed — the job queue is unreachable",
                )
            )
        logger.exception("Failed to enqueue reembed_all_pages_task")
        raise HTTPException(
            status_code=500,
            detail="Failed to enqueue job — the job queue is unreachable.",
        ) from exc

    return EmbeddingSwitchOut(job_id=job_id)


@router.get(
    "/settings/embeddings/jobs/{job_id}", response_model=EmbeddingJobOut
)
async def get_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("org:settings:manage"),
):
    job = await db.get(EmbeddingJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return _job_to_out(job)


@router.post(
    "/settings/embeddings/jobs/{job_id}/cancel",
    response_model=EmbeddingJobOut,
)
async def cancel_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("org:settings:manage"),
):
    job = await db.get(EmbeddingJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status not in ("pending", "running"):
        raise HTTPException(
            status_code=400, detail=f"Job is {job.status}, cannot cancel"
        )
    job.status = "cancelled"
    job.finished_at = datetime.now(timezone.utc)
    await db.commit()
    return _job_to_out(job)
