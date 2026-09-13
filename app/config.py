"""
Application configuration loaded from environment variables.
"""


from pydantic import Field, model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """
    Infrastructure settings loaded from .env or environment.

    Most AI provider settings live in app_config (Admin Portal). Omniroute is
    the exception: OMNIROUTE_API_KEY / OMNIROUTE_BASE_URL / OMNIROUTE_MODEL
    seed the LLM slot when nothing is saved in the database yet.
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
    minio_presign_expiry_minutes: int = Field(
        default=30,
        description="Lifetime of a presigned download URL. Was 24 hours, and a fresh URL "
                    "was minted on EVERY source-detail fetch — after issuance the URL is an "
                    "unauthenticated bearer capability that survives permission revocation "
                    "and account deletion, so a link captured from a proxy log or browser "
                    "history granted a full day of access.",
    )

    # --- Uploads ---
    max_upload_mb: int = Field(
        default=100,
        description="Per-file upload cap enforced in app/services/upload_guard.py. "
                    "nginx allows 500 MB on /api/, and the API is one uvicorn process "
                    "with a 2 GB container limit, so an unbounded read was an OOM.",
    )
    max_zip_upload_mb: int = Field(
        default=200,
        description="Cap for .zip archive uploads before extraction.",
    )
    max_request_body_mb: int = Field(
        default=256,
        description="Hard cap on any request body, enforced by BodySizeLimitMiddleware "
                    "before a route runs. The route-level guards only fire after the ASGI "
                    "server has already received the whole body, so nginx's 500 MB was the "
                    "real limit. Must stay above max_zip_upload_mb plus multipart framing.",
    )

    # --- Zip extraction (app/services/zip_service.py) ---
    # Caps on the *decompressed* archive. The upload cap above bounds the compressed
    # bytes only, and zeroed files compress ~1000:1, so a 200 MB archive can still
    # inflate to gigabytes.
    max_zip_entries: int = Field(
        default=50,
        description="Maximum number of files extracted from one archive; bounds DB "
                    "connection and worker-queue pressure from a many-entry archive.",
    )
    max_zip_member_mb: int = Field(
        default=50,
        description="Per-entry decompressed cap. Enforced on bytes actually inflated, "
                    "not on the archive's self-declared entry size.",
    )
    max_zip_total_mb: int = Field(
        default=500,
        description="Aggregate decompressed cap for one archive. Entries are spooled to "
                    "a temp file rather than held on the heap, so this bounds disk, not RSS.",
    )

    # --- Skill contribution workspaces ---
    max_contribution_text_kb: int = Field(
        default=1024,
        description="Cap on PutFileRequest.content for the text PUT path. It arrives as a "
                    "JSON string, so no multipart guard applies to it.",
    )
    max_contribution_total_mb: int = Field(
        default=25,
        description="Cumulative byte budget for one contribution's workspace. Per-file caps "
                    "bound a single request; without this a contributor can repeat an "
                    "under-cap write under fresh paths until storage fills.",
    )
    max_contribution_files: int = Field(
        default=200,
        description="Cumulative file-count budget for one contribution's workspace. Above "
                    "the 100-entry cap on ZIP-created contributions so those stay editable.",
    )

    # --- MCP tokens ---
    mcp_token_expiry_days: int = Field(
        default=90,
        description="Lifetime of a generated MCP bearer token. Enforced in "
                    "MCPAuthService.verify_token; expired tokens are rejected.",
    )

    # --- MCP pagination caps (app/mcp/tools.py) ---
    # The list tools take `limit`/`offset` straight from an LLM-generated tool call, so
    # they are attacker- *and* accident-controlled. Four of them passed the value through
    # unbounded: `limit=1_000_000` issued a real `LIMIT 1000000` inside the same 2 GB
    # container that serves the API, and `limit=-1` reached Postgres as a syntax error.
    mcp_max_page_size: int = Field(
        default=200,
        ge=1,
        le=1_000,
        description="Largest `limit` an MCP list tool will accept. A larger request is "
                    "rejected with an explanatory message rather than clamped, so the "
                    "caller knows it has to paginate instead of silently seeing a "
                    "truncated page it believes is complete.",
    )
    mcp_max_offset: int = Field(
        default=10_000,
        ge=0,
        le=1_000_000,
        description="Largest `offset` an MCP list tool will accept. Deep offsets are a "
                    "sequential scan in Postgres, and list_pending_drafts multiplies the "
                    "window before it hits the database.",
    )
    mcp_max_draft_scan: int = Field(
        default=1_000,
        ge=50,
        le=20_000,
        description="Hard ceiling on rows list_pending_drafts pre-fetches before "
                    "per-draft permission filtering. The prefetch is (offset+limit)*4 "
                    "because filtering drops rows; this bounds it. Hitting the ceiling "
                    "is reported in the tool output, never silently truncated.",
    )

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

    # --- Per-job timeout overrides (app/worker.py WorkerSettings.functions) ---
    # worker_job_timeout is the default for every job. These two jobs are bulk loops whose
    # runtime scales with corpus size rather than with one document, so inheriting the
    # default made them unable to finish at all on a large input: arq cancels at the
    # timeout, and before the BaseException handlers below existed that cancellation left
    # no terminal status anywhere.
    reembed_job_timeout: int = Field(
        default=14_400,
        ge=600,
        le=86_400,
        description="Timeout for reembed_all_pages_task. Re-embedding the whole wiki is "
                    "one HTTP round-trip per 50 pages against an external embedding API, "
                    "so the wall time is a function of page count, not of any one page.",
    )
    caption_job_timeout: int = Field(
        default=14_400,
        ge=300,
        le=86_400,
        description="Timeout for caption_images_task. At the default concurrency and "
                    "per-image timeout, 400 images is ~3.3 h — longer than the 3600 s "
                    "this job used to be pinned to, which meant an image-heavy document "
                    "could never finish captioning.",
    )
    caption_max_concurrency: int = Field(
        default=4,
        ge=1,
        le=16,
        description="Concurrent vision calls inside caption_images_task. Raise it (and "
                    "watch the provider's rate limit) to shorten a large document's run.",
    )
    caption_per_image_timeout: int = Field(
        default=120,
        ge=10,
        le=600,
        description="Per-image ceiling on one vision call inside caption_images_task.",
    )

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

    # --- Omniroute (OpenAI-compatible proxy) ---
    # Seeds the LLM provider when Admin Settings has no llm_provider yet.
    # A saved Settings value always wins (DB > env).
    omniroute_api_key: str = Field(
        default="",
        description="Bearer token for the Omniroute proxy (OMNIROUTE_API_KEY).",
    )
    omniroute_base_url: str = Field(
        default="",
        description="OpenAI-compatible base URL, e.g. https://ai.nosiaht.com/v1",
    )
    omniroute_model: str = Field(
        default="",
        description="Default Omniroute model id, e.g. nosiaht or glm/glm-5.3",
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

    # --- Development escape hatches ---
    # Declared as real settings fields so they can be set in .env / .env.local as the
    # docs instruct. Reading them via os.environ made a dotenv entry silently invisible,
    # because pydantic-settings never writes to os.environ.
    arkon_allow_default_secret: bool = Field(
        default=False,
        description="Skip the weak-secret validation below. Development only.",
    )
    arkon_allow_cors_wildcard: bool = Field(
        default=False,
        description="Permit CORS_ORIGINS='*' together with credentials. Development only.",
    )

    # Later files win, so .env.local overrides .env for local development.
    model_config = {
        "env_file": (".env", ".env.local"),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    @model_validator(mode="after")
    def validate_secrets(self):
        if self.arkon_allow_default_secret:
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

        # Each credential is checked independently. The previous `and` over one exact
        # pair meant the values this repo actually ships in .env.docker.example
        # (minioadmin / change-me-minio-secret-key) passed validation cleanly.
        #
        # Both sets must list the placeholder .env.docker.example actually ships. The
        # access set was missing `change-me-minio-access-key` while the secret set carried
        # its counterpart, so the file's own claim — "Both MinIO credentials are rejected
        # at startup if left at a known default" — was false for one of the two. An
        # operator fixes what the app rejects and stops, shipping the placeholder as
        # MINIO_ROOT_USER.
        weak_minio_access = {"minioadmin", "change-me-minio-access-key"}
        weak_minio_secret = {"minioadmin123", "change-me-minio-secret-key"}
        if self.minio_access_key in weak_minio_access:
            raise ValueError(
                "MINIO_ACCESS_KEY is a known default. Set a unique value in your .env file."
            )
        if self.minio_secret_key in weak_minio_secret:
            raise ValueError(
                "MINIO_SECRET_KEY is a known default. Set a unique value in your .env file."
            )
        if self.redis_password in {"", "change-me-redis-password"}:
            raise ValueError(
                "REDIS_PASSWORD is unset or a known default. Set a unique value in your .env file."
            )
        if "arkon_secret" in self.database_url or "change-me-postgres-password" in self.database_url:
            raise ValueError(
                "DATABASE_URL still embeds a default password. Set a unique value in your .env file."
            )
        return self

    @model_validator(mode="after")
    def validate_upload_limit_relationships(self):
        # A body cap below a route cap makes the route cap unreachable: the middleware
        # would 413 an upload the endpoint is documented to accept, and the failure would
        # only show up for users uploading near the route limit.
        largest_route_cap = max(self.max_upload_mb, self.max_zip_upload_mb)
        if self.max_request_body_mb < largest_route_cap:
            raise ValueError(
                f"MAX_REQUEST_BODY_MB ({self.max_request_body_mb}) is below the largest "
                f"per-route upload cap ({largest_route_cap} MB), so those uploads could "
                "never succeed. Raise it above the route caps."
            )
        if self.max_zip_member_mb > self.max_zip_total_mb:
            raise ValueError("MAX_ZIP_MEMBER_MB must not exceed MAX_ZIP_TOTAL_MB")
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
