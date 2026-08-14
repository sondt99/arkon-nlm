"""
Application configuration loaded from environment variables.
"""


from pydantic import Field, model_validator
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
        default="change-me-admin-password",
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
    # Empty = same-origin only (safe default for the nginx-fronted setup).
    # Set to explicit origin(s) only if the API is called cross-origin.
    cors_origins: str = Field(default="")

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
    mrp_ingestion_model_id: str = Field(
        default="",
        description="Optional explicit model ID for document ingestion; avoids unstable router aliases",
    )
    mrp_chunk_target_chars: int = Field(default=12_000, ge=4_000, le=40_000)
    mrp_chunk_overlap_chars: int = Field(default=1_000, ge=0, le=4_000)
    mrp_map_max_concurrency: int = Field(default=6, ge=1, le=16)
    mrp_extract_timeout: int = Field(default=120, ge=30, le=600)
    mrp_entity_merge_threshold: float = Field(default=0.90, ge=0.80, le=0.99)
    mrp_entity_ambiguous_threshold: float = Field(default=0.75, ge=0.50, le=0.90)
    mrp_kb_update_threshold: float = Field(default=0.82, ge=0.70, le=0.99)
    mrp_kb_maybe_threshold: float = Field(default=0.48, ge=0.30, le=0.80)
    mrp_kb_min_semantic_similarity: float = Field(default=0.72, ge=0.50, le=0.95)
    mrp_kb_min_lexical_similarity: float = Field(default=0.72, ge=0.50, le=0.95)
    mrp_writer_max_concurrency: int = Field(default=4, ge=1, le=12)
    mrp_writer_timeout: int = Field(default=300, ge=60, le=900)
    mrp_writer_max_attempts: int = Field(default=3, ge=1, le=5)
    mrp_verify_conflict_threshold: float = Field(default=0.80, ge=0.65, le=0.99)
    mrp_verify_min_mentions: int = Field(default=3, ge=1, le=20)
    mrp_merge_min_body_ratio: float = Field(default=0.70, ge=0.50, le=1.0)
    mrp_merge_timeout: int = Field(default=120, ge=30, le=600)

    # --- Chatbot latency / context controls ---
    chat_rag_top_k: int = Field(default=4, ge=1, le=10)
    chat_linked_pages_limit: int = Field(default=2, ge=0, le=5)
    chat_context_chars_per_page: int = Field(default=2_500, ge=500, le=5_000)
    chat_history_messages: int = Field(default=6, ge=0, le=20)
    chat_generation_timeout: int = Field(default=240, ge=30, le=280)
    chat_min_detailed_answer_chars: int = Field(default=1_800, ge=400, le=8_000)
    chat_expand_short_answers: bool = Field(default=True)

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

    @model_validator(mode="after")
    def validate_secrets(self):
        import os
        import sys
        bypass = "pytest" in sys.modules or os.environ.get("ARKON_ALLOW_DEFAULT_SECRET") == "1"
        if bypass:
            return self

        if self.secret_key == "change-me-to-a-random-secret-string":
            raise ValueError(
                "SECRET_KEY is still the default value. Set a strong random secret "
                "in your .env file. To bypass in development, set ARKON_ALLOW_DEFAULT_SECRET=1."
            )
        weak_admin = {"change-me-admin-password", "admin123", "admin", "password"}
        if self.default_admin_password in weak_admin:
            raise ValueError(
                "DEFAULT_ADMIN_PASSWORD is a weak default. Set a strong password in your .env file."
            )
        if self.minio_access_key == "minioadmin" and self.minio_secret_key == "minioadmin123":
            raise ValueError(
                "MINIO_ACCESS_KEY / MINIO_SECRET_KEY are still MinIO factory defaults. "
                "Set unique credentials in your .env file."
            )
        return self

    @model_validator(mode="after")
    def validate_mrp_accuracy_relationships(self):
        if self.mrp_chunk_overlap_chars >= self.mrp_chunk_target_chars:
            raise ValueError("MRP chunk overlap must be smaller than the chunk target")
        if self.mrp_kb_maybe_threshold >= self.mrp_kb_update_threshold:
            raise ValueError("MRP KB MAYBE threshold must be lower than UPDATE threshold")
        if self.mrp_entity_ambiguous_threshold >= self.mrp_entity_merge_threshold:
            raise ValueError("MRP ambiguous threshold must be lower than entity merge threshold")
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        """Parse CORS_ORIGINS into a list."""
        value = self.cors_origins.strip()
        if value == "*":
            return ["*"]
        return [o.strip() for o in value.split(",") if o.strip()]


settings = Settings()
