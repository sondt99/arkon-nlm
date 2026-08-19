"""
Arkon — Enterprise AI Control Center.
FastAPI application entry point.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from app.config import settings
from app.mcp.server import create_mcp_server
from app.services.upload_guard import BodySizeLimitMiddleware

# Named so the health endpoints below read as intent rather than as a magic number.
HTTP_503 = 503

# Create the MCP server and its HTTP app (lifespan must be composed with FastAPI)
mcp_server = create_mcp_server()
mcp_http_app = mcp_server.http_app(path="/", stateless_http=True)


async def seed_default_admin() -> bool:
    """Create default admin account from .env if no admin exists yet.

    Returns False if the step could not run, so startup can report that instead of
    claiming success. A failure here almost always means the database is unreachable.
    """
    from sqlalchemy import select

    from app.database import async_session_factory
    from app.database.models import Department, Employee
    from app.services.auth_service import hash_password_async

    try:
        async with async_session_factory() as session:
            # Check if any admin already exists
            stmt = select(Employee).where(Employee.role == "admin").limit(1)
            result = await session.execute(stmt)
            if result.scalar_one_or_none():
                return True  # Admin already exists, skip

            # Create admin department
            dept = Department(name="Administration", description="System administrators")
            session.add(dept)
            await session.flush()

            # Create admin user from .env. bcrypt at cost 12 is ~250 ms of uninterruptible
            # CPU, and this runs on the event loop during lifespan startup, so the inline
            # form delayed every other startup step and the first requests behind it.
            admin = Employee(
                name="Admin",
                email=settings.default_admin_email,
                password_hash=await hash_password_async(settings.default_admin_password),
                role="admin",
                department_id=dept.id,
            )
            session.add(admin)
            await session.flush()

            await session.commit()
            logger.success(f"Default admin created: {settings.default_admin_email}")
            return True
    except Exception as e:
        logger.warning(f"Could not seed default admin: {e}")
        return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup & shutdown logic (composed with FastMCP lifespan)."""
    async with mcp_http_app.lifespan(app):
        logger.info("Starting Arkon API...")

        # Startup stays non-fatal on purpose — the API has to come up so /health can report
        # *why* it is unhealthy. What is not acceptable is what this block used to do:
        # swallow every failure into a warning and then log "started successfully"
        # regardless. Each failed step is recorded and named in the final line instead.
        degraded: list[str] = []

        # Ensure MinIO bucket exists. This is the one place that may create it; the health
        # probe uses the read-only check so a 15-second liveness poll cannot make a bucket.
        try:
            from app.services.storage_service import storage_service
            await storage_service.ensure_bucket()
            logger.success("MinIO bucket ready")
        except Exception as e:
            logger.warning(f"MinIO not available yet: {e}")
            degraded.append("minio-bucket")

        # Seed default admin if no admin exists yet
        if not await seed_default_admin():
            degraded.append("default-admin-seed")

        # Seed built-in skills (idempotent — no-op if already up to date)
        try:
            from app.scripts.seed_skills import seed_builtin_skills
            await seed_builtin_skills()
        except Exception as e:
            logger.warning(f"Could not seed built-in skills: {e}")
            degraded.append("builtin-skills-seed")

        # Seed default extraction hints for security knowledge types (idempotent)
        try:
            from app.scripts.seed_security_kt_hints import seed_security_kt_hints
            await seed_security_kt_hints()
        except Exception as e:
            logger.warning(f"Could not seed security KT extraction hints: {e}")
            degraded.append("security-kt-hints-seed")

        # Warn if sensitive defaults are unchanged
        if settings.secret_key == "change-me-to-a-random-secret-string":
            logger.warning("⚠️  SECRET_KEY is set to the default value — change it before deploying to production!")
        if settings.default_admin_password == "change-me-admin-password":
            logger.warning("⚠️  DEFAULT_ADMIN_PASSWORD is unchanged — change the admin password after first login!")
        if "*" in settings.cors_origin_list:
            if not settings.arkon_allow_cors_wildcard:
                raise RuntimeError(
                    "CORS_ORIGINS is '*' with credentials enabled — any website can call this "
                    "API using a user's token. Set CORS_ORIGINS to your actual frontend origin(s). "
                    "To bypass in development, set ARKON_ALLOW_CORS_WILDCARD=1."
                )
            logger.warning(
                "⚠️  CORS_ORIGINS is '*' (development bypass active) — do NOT use in production!"
            )

        # MCP server ready
        logger.success("Arkon MCP Server ready at /mcp")
        if degraded:
            logger.warning(
                "Arkon API started with failed startup steps: "
                f"{', '.join(degraded)} — /health reports the live dependency state"
            )
        else:
            logger.success("Arkon API started successfully")
        yield

        logger.info("Arkon API shutdown complete")


app = FastAPI(
    title="Arkon API",
    description="Enterprise AI Control Center — Knowledge Base & Skill Management",
    version="0.1.0",
    lifespan=lifespan,
)

# --- Request body size limit ---
# Added before CORS so it ends up *inside* CORSMiddleware: middleware added later is
# outermost, and a 413 emitted outside CORS would reach a browser without the
# Access-Control-Allow-Origin header, i.e. as an opaque network error rather than a
# readable status. Route-level guards cannot replace this — they only run once the ASGI
# server has already received the whole body.
app.add_middleware(BodySizeLimitMiddleware)

# --- CORS ---
logger.info(f"Allowed CORS origins: {settings.cors_origin_list}")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Total-Count"],
)

# --- Mount MCP Server ---
# Claude Desktop connects to: https://your-server/mcp
app.mount("/mcp", mcp_http_app)

# --- REST API Routers ---
from app.routers import (  # noqa: E402
    admin_embeddings,
    admin_settings,
    audit,
    auth,
    chat,
    claude_gateway,
    export_api,
    knowledge_types,
    notebooklm,
    notes,
    projects,
    rbac,
    roles,
    skill_contributions,
    skills,
    sources,
    wiki,
    wiki_drafts,
    wiki_images,
)


@app.exception_handler(claude_gateway.AnthropicError)
async def _anthropic_error_handler(_request, exc: claude_gateway.AnthropicError):
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=exc.status_code,
        content={"type": "error", "error": {"type": exc.error_type, "message": exc.message}},
    )


app.include_router(auth.router, prefix="/api", tags=["auth"])
app.include_router(sources.router, prefix="/api", tags=["sources"])
app.include_router(notes.router, prefix="/api", tags=["notes"])
app.include_router(wiki_drafts.router, prefix="/api", tags=["wiki-drafts"])
app.include_router(wiki.router, prefix="/api", tags=["wiki"])
app.include_router(wiki_images.router, prefix="/api", tags=["wiki"])
app.include_router(admin_settings.router, prefix="/api", tags=["settings"])
app.include_router(admin_embeddings.router, prefix="/api", tags=["settings"])
app.include_router(rbac.router, prefix="/api", tags=["rbac"])
app.include_router(knowledge_types.router, prefix="/api", tags=["knowledge-types"])
app.include_router(projects.router, prefix="/api", tags=["projects"])
app.include_router(roles.router, prefix="/api", tags=["roles"])
app.include_router(audit.router, prefix="/api", tags=["audit"])
app.include_router(skills.router, prefix="/api", tags=["skills"])
app.include_router(skill_contributions.router, prefix="/api", tags=["skill-contributions"])
app.include_router(notebooklm.router, prefix="/api", tags=["notebooklm"])
app.include_router(chat.router, prefix="/api", tags=["chat"])
app.include_router(export_api.router, prefix="/api", tags=["export-api"])
app.include_router(claude_gateway.router, prefix="/api", tags=["claude-gateway"])


@app.get("/")
async def root():
    return {
        "name": "Arkon",
        "description": "Enterprise AI Control Center",
        "version": "0.1.0",
        "mcp_endpoint": "/mcp",
        "docs": "/docs",
    }


async def _check_database() -> str:
    from sqlalchemy import text

    from app.database import async_session_factory
    try:
        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
        return "healthy"
    except Exception as e:
        logger.warning(f"Health check — database error: {e}")
        return "error"


async def _check_redis() -> str:
    try:
        import redis.asyncio as aioredis
        r = aioredis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            password=settings.redis_password or None,
            db=settings.redis_db,
            socket_connect_timeout=2,
        )
        try:
            await r.ping()
        finally:
            await r.aclose()
        return "healthy"
    except Exception as e:
        logger.warning(f"Health check — redis error: {e}")
        return "error"


async def _check_minio() -> str:
    """Read-only. Must never be `ensure_bucket()` — see StorageService.bucket_exists_sync."""
    try:
        from app.services.storage_service import storage_service
        if not await storage_service.bucket_exists():
            logger.warning(
                f"Health check — MinIO bucket '{settings.minio_bucket}' does not exist"
            )
            return "bucket_missing"
        return "healthy"
    except Exception as e:
        logger.warning(f"Health check — minio error: {e}")
        return "error"


@app.get("/health")
async def health(response: Response):
    """Container liveness probe. **200 only when every dependency answers.**

    Both health endpoints used to return 200 unconditionally, and Compose's probe only
    fails on a non-2xx status. So Postgres could be unreachable while Docker reported the
    API healthy, `depends_on: service_healthy` stayed satisfied, and the workers and
    frontend started against a database that was not there.
    """
    services = {
        "database": await _check_database(),
        "redis": await _check_redis(),
        "minio": await _check_minio(),
    }
    degraded = [name for name, state in services.items() if state != "healthy"]
    if degraded:
        response.status_code = HTTP_503
    return {
        "status": "degraded" if degraded else "healthy",
        "services": services,
    }


@app.get("/api/health")
async def api_health(response: Response):
    """Detailed health check for API, database, and worker (Redis).

    Returns 503 when a dependency is down, for the same reason as `/health`: a monitor
    pointed at this path would otherwise read 200 and conclude all was well. The body
    still carries per-service detail so a caller that inspects it (the dashboard card
    reads it off the thrown ApiError) can say *which* dependency failed.
    """
    result = {
        "api": "healthy",
        "database": await _check_database(),
        "worker": await _check_redis(),
    }
    if any(state != "healthy" for state in result.values()):
        response.status_code = HTTP_503
    return result
