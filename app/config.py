"""
Application configuration loaded from environment variables.
"""


from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """
    Infrastructure settings loaded from .env or environment.
    
    AI provider settings (embedding, LLM, vision) are NOT here —
    they are stored in the database and managed via Admin Portal.
    See: app/services/config_service.py and app/ai/registry.py
    """

    # --- Database ---
    database_url: str = Field(
        default="postgresql+asyncpg://arkon:arkon_secret@localhost:5432/arkon",
        description="PostgreSQL connection string (async)",
    )

    # --- Auth ---
    secret_key: str = Field(
        default="change-me-to-a-random-secret-string",
        description="Secret key for signing JWT tokens and encrypting config values",
    )
    default_admin_email: str = Field(
        default="admin@arkon.local",
        description="Email for the initial admin account (created on first startup)",
    )
    default_admin_password: str = Field(
        default="admin123",
        description="Password for the initial admin account",
    )

    # --- MinIO ---
    minio_endpoint: str = Field(default="localhost:9000")
    minio_public_endpoint: str = Field(
        default="",
        description="Public-facing MinIO address used in presigned URLs (browser-accessible). "
                    "Defaults to minio_endpoint if not set. "
                    "In Docker: set to 'localhost:9000' so presigned URLs work from the browser.",
    )
    minio_access_key: str = Field(default="minioadmin")
    minio_secret_key: str = Field(default="minioadmin123")
    minio_bucket: str = Field(default="arkon-files")
    minio_secure: bool = Field(default=False)
    minio_presign_expiry_hours: int = Field(default=24)

    # --- CORS ---
    cors_origins: str = Field(default="*")

    # --- Redis (arq worker queue) ---
    redis_host: str = Field(default="localhost")
    redis_port: int = Field(default=6379)
    redis_password: str = Field(default="")
    redis_db: int = Field(default=0)
    worker_max_jobs: int = Field(default=3, description="Max concurrent ingestion jobs")
    worker_job_timeout: int = Field(default=3600, description="Job timeout in seconds")

    # --- MRP Pipeline ---
    mrp_auto_approve_plan: bool = Field(
        default=False,
        description="If True, compilation plans are auto-approved without human review",
    )

    # --- NotebookLM Integration ---
    notebooklm_storage_path: str = Field(
        default="",
        description=(
            "Path to the notebooklm-py session storage directory "
            "(contains cookies/config). Defaults to OS default (~/.config/notebooklm or %%APPDATA%%\\notebooklm). "
            "Set to an absolute path when running in Docker so sessions persist across restarts."
        ),
    )

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    @property
    def cors_origin_list(self) -> list[str]:
        """Parse CORS_ORIGINS into a list."""
        value = self.cors_origins.strip()
        if value == "*":
            return ["*"]
        return [o.strip() for o in value.split(",") if o.strip()]


settings = Settings()
