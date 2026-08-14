"""
Admin settings router — provider config, connection testing, dashboard stats.
"""

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.database.models import Department, Employee, Source
from app.database.repository import Repository
from app.services.audit_service import log_audit
from app.services.auth_service import get_current_user, require_permission

router = APIRouter()


# ---------------------------------------------------------------------------
# Dashboard stats
# ---------------------------------------------------------------------------

class DashboardStats(BaseModel):
    total_sources: int
    total_departments: int
    total_employees: int


@router.get("/dashboard/stats", response_model=DashboardStats)
async def dashboard_stats(
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("org:settings:manage"),
):
    repo = Repository(db)
    return DashboardStats(
        total_sources=await repo.count(Source),
        total_departments=await repo.count(Department),
        total_employees=await repo.count(Employee),
    )


# ---------------------------------------------------------------------------
# Settings CRUD
# ---------------------------------------------------------------------------

class SettingsUpdate(BaseModel):
    """Batch update config values."""
    settings: dict[str, str]


class TestConnectionResult(BaseModel):
    success: bool
    message: str
    details: Optional[dict] = None


@router.get("/settings")
async def get_settings(
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("org:settings:manage"),
):
    """Get current app settings (masked sensitive values for UI)."""
    from app.services.config_service import ConfigService

    svc = ConfigService(db)
    ui_config = await svc.get_all_for_ui()
    return ui_config


@router.put("/settings")
async def update_settings(
    body: SettingsUpdate,
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("org:settings:manage"),
):
    """Update config values in database."""
    from app.services.config_service import ConfigService

    svc = ConfigService(db)
    results = await svc.set_batch(body.settings)
    
    # Audit log
    keys_updated = list(body.settings.keys())
    await log_audit(db, _user, "update", "settings", "global", reason=f"Updated keys: {', '.join(keys_updated)}")
    await db.commit()
    return {"updated": results}


# ---------------------------------------------------------------------------
# Provider connection testing
# ---------------------------------------------------------------------------

@router.post("/settings/test-providers", response_model=dict[str, TestConnectionResult])
async def test_all_providers(
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("org:settings:manage"),
):
    """Test all configured AI providers (embedding, LLM, vision)."""
    from app.ai.registry import ProviderRegistry

    registry = ProviderRegistry(db)
    results = await registry.test_all()

    return {
        capability: TestConnectionResult(success=ok, message=msg)
        for capability, (ok, msg) in results.items()
    }


@router.post("/settings/test-embedding", response_model=TestConnectionResult)
async def test_embedding(
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("org:settings:manage"),
):
    """Test the configured embedding provider."""
    from app.ai.registry import ProviderRegistry

    try:
        registry = ProviderRegistry(db)
        provider = await registry.get_embedding()
        ok, msg = await provider.test_connection()
        return TestConnectionResult(success=ok, message=msg)
    except Exception as e:
        return TestConnectionResult(success=False, message=str(e))


@router.post("/settings/test-llm", response_model=TestConnectionResult)
async def test_llm(
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("org:settings:manage"),
):
    """Test the configured LLM provider."""
    from app.ai.registry import ProviderRegistry

    try:
        registry = ProviderRegistry(db)
        provider = await registry.get_llm()
        ok, msg = await provider.test_connection()
        return TestConnectionResult(success=ok, message=msg)
    except Exception as e:
        return TestConnectionResult(success=False, message=str(e))


@router.post("/settings/test-vision", response_model=TestConnectionResult)
async def test_vision(
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("org:settings:manage"),
):
    """Test the configured vision provider."""
    from app.ai.registry import ProviderRegistry

    try:
        registry = ProviderRegistry(db)
        provider = await registry.get_vision()
        if not provider:
            return TestConnectionResult(success=False, message="No vision provider configured")
        ok, msg = await provider.test_connection()
        return TestConnectionResult(success=ok, message=msg)
    except Exception as e:
        return TestConnectionResult(success=False, message=str(e))


@router.post("/settings/test-chatbot", response_model=TestConnectionResult)
async def test_chatbot(
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("org:settings:manage"),
):
    """Test the configured chatbot LLM provider (falls back to main LLM if not set)."""
    from app.ai.registry import ProviderRegistry

    try:
        registry = ProviderRegistry(db)
        provider = await registry.get_chatbot_llm()
        ok, msg = await provider.test_connection()
        return TestConnectionResult(success=ok, message=msg)
    except Exception as e:
        return TestConnectionResult(success=False, message=str(e))


@router.post("/settings/test-gateway", response_model=TestConnectionResult)
async def test_gateway(
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("org:settings:manage"),
):
    """Test the configured Claude Code Gateway provider (falls back to main LLM if not set)."""
    from app.ai.registry import ProviderRegistry

    try:
        registry = ProviderRegistry(db)
        provider = await registry.get_gateway_llm()
        ok, msg = await provider.test_connection()
        return TestConnectionResult(success=ok, message=msg)
    except Exception as e:
        return TestConnectionResult(success=False, message=str(e))


# ---------------------------------------------------------------------------
# Supported providers list (for admin UI dropdowns)
# ---------------------------------------------------------------------------

@router.get("/settings/providers")
async def list_providers(
    _user: Employee = Depends(get_current_user),
):
    """Get supported providers and models for each capability."""
    from app.ai.registry import SUPPORTED_PROVIDERS
    return SUPPORTED_PROVIDERS


# ---------------------------------------------------------------------------
# Fetch model list from an OpenAI-compatible /v1/models endpoint
# ---------------------------------------------------------------------------

class FetchModelsBody(BaseModel):
    base_url: str
    api_key: str = ""


class FetchModelsResult(BaseModel):
    models: list[str]


def _validate_external_url(raw_url: str) -> str:
    """Block requests to private/internal networks (SSRF prevention)."""
    import ipaddress
    import socket
    from urllib.parse import urlparse

    from fastapi import HTTPException

    parsed = urlparse(raw_url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="Only http/https URLs are allowed")

    hostname = parsed.hostname
    if not hostname:
        raise HTTPException(status_code=400, detail="Invalid URL: no hostname")

    blocked_hosts = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}
    if hostname.lower() in blocked_hosts:
        raise HTTPException(status_code=400, detail="Requests to localhost are not allowed")

    try:
        resolved = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror:
        raise HTTPException(status_code=400, detail=f"Cannot resolve hostname: {hostname}")

    for _, _, _, _, addr in resolved:
        ip = ipaddress.ip_address(addr[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise HTTPException(status_code=400, detail="Requests to private/internal networks are not allowed")

    return raw_url


@router.post("/settings/fetch-models", response_model=FetchModelsResult)
async def fetch_models_from_url(
    body: FetchModelsBody,
    _user: Employee = require_permission("org:settings:manage"),
):
    """Fetch model list from any OpenAI-compatible /v1/models endpoint."""
    import httpx
    from fastapi import HTTPException

    _validate_external_url(body.base_url)
    url = body.base_url.rstrip("/") + "/models"
    headers: dict[str, str] = {}
    if body.api_key:
        headers["Authorization"] = f"Bearer {body.api_key}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        models = sorted([
            m["id"] for m in data.get("data", [])
            if isinstance(m, dict) and "id" in m
        ])
        return FetchModelsResult(models=models)

    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=400,
            detail=f"API returned {e.response.status_code}",
        )
    except httpx.ConnectError:
        raise HTTPException(status_code=400, detail="Cannot connect to the provided URL")
    except httpx.TimeoutException:
        raise HTTPException(status_code=400, detail="Connection timed out")
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to fetch models")
