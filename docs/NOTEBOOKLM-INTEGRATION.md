# Kế hoạch tích hợp NotebookLM vào Arkon

> **Trạng thái**: Kế hoạch — chưa implement  
> **Mục tiêu**: Cho phép người dùng upload tài liệu lên Google NotebookLM, tạo tóm tắt chi tiết (study guide / report), và tự động đồng bộ nội dung đó vào Arkon wiki.

---

## Tổng quan

NotebookLM (Google) có khả năng phân tích tài liệu và tạo ra **study guide** ở định dạng Markdown — bổ sung rất tốt cho pipeline MRP hiện tại của Arkon. Tích hợp này không thay thế MRP mà là **enrichment layer** thêm bên cạnh:

```
PDF/DOCX upload
    ↓
[Arkon MRP Pipeline]          [NotebookLM Sync] ← tùy chọn
    ↓                               ↓
Arkon wiki pages ←── merge ──── NLM report (markdown)
```

### Lợi ích

| Arkon MRP | NotebookLM |
|---|---|
| Trích xuất có cấu trúc theo knowledge type | Tóm tắt toàn diện, ngôn ngữ tự nhiên |
| Tạo entity pages, concept pages | Study guide, key points, timeline |
| Liên kết cross-reference giữa wiki pages | Citation rõ ràng từ nguồn |
| Xử lý batch nhiều tài liệu | Phân tích sâu một tài liệu |

---

## Kiến trúc tổng thể

### Các thành phần mới

```
app/
  services/
    notebooklm_service.py       ← wrapper gọi notebooklm-py CLI
  workers/
    notebooklm_tasks.py         ← arq tasks cho sync
  routers/
    notebooklm.py               ← API endpoints
  database/
    migrations/
      versions/020_notebooklm_columns.py  ← migration
frontend/
  src/
    components/
      upload-dialog.tsx          ← thêm checkbox NLM
      knowledge-table.tsx        ← thêm action "Sync to NotebookLM"
      notebooklm-status.tsx      ← badge hiển thị trạng thái NLM
```

### Luồng dữ liệu

```
1. User upload → POST /api/sources/upload
       ↓ (nếu nlm_enabled=true)
2. API enqueue → notebooklm_sync_task(source_id)
       ↓ (arq worker)
3. Worker → download file từ MinIO
       ↓
4. notebooklm source add <file> -n <notebook_id>
       ↓
5. source wait → source ready
       ↓
6. notebooklm generate report --format study-guide
       ↓
7. artifact wait → artifact complete (5-15 phút)
       ↓
8. notebooklm download report ./report.md
       ↓
9. Parse markdown → tạo/cập nhật wiki pages với tag "nlm-report"
       ↓
10. Cập nhật source.notebooklm_status = "synced"
        ↓
11. Cleanup: xóa notebook NLM (tùy cấu hình)
```

---

## Phase 1: Nền tảng (Manual trigger)

### 1.1 Database Migration

**File**: `app/database/migrations/versions/020_notebooklm_columns.py`

```python
"""add notebooklm columns to sources

Revision ID: 020
Revises: 019
Create Date: 2026-05-14
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "020"
down_revision = "019"

def upgrade():
    op.add_column("sources", sa.Column("notebooklm_notebook_id", sa.String(255), nullable=True))
    op.add_column("sources", sa.Column("notebooklm_artifact_id", sa.String(255), nullable=True))
    op.add_column("sources", sa.Column(
        "notebooklm_status",
        sa.Enum("pending", "uploading", "generating", "synced", "error", name="nlm_status_enum"),
        nullable=True
    ))
    op.add_column("sources", sa.Column("notebooklm_error", sa.Text(), nullable=True))
    op.add_column("sources", sa.Column("notebooklm_synced_at", sa.DateTime(timezone=True), nullable=True))

def downgrade():
    op.drop_column("sources", "notebooklm_notebook_id")
    op.drop_column("sources", "notebooklm_artifact_id")
    op.drop_column("sources", "notebooklm_status")
    op.drop_column("sources", "notebooklm_error")
    op.drop_column("sources", "notebooklm_synced_at")
    op.execute("DROP TYPE nlm_status_enum")
```

### 1.2 ORM Model Update

**File**: `app/database/models.py` — thêm vào class `Source`:

```python
# NotebookLM integration
notebooklm_notebook_id = Column(String(255), nullable=True)
notebooklm_artifact_id = Column(String(255), nullable=True)
notebooklm_status = Column(
    Enum("pending", "uploading", "generating", "synced", "error", name="nlm_status_enum"),
    nullable=True
)
notebooklm_error = Column(Text, nullable=True)
notebooklm_synced_at = Column(DateTime(timezone=True), nullable=True)
```

### 1.3 Authentication & Configuration

NotebookLM dùng cookie-based auth. Trong môi trường Docker:

**Cách lấy credentials:**
```bash
# Trên máy host (có browser)
notebooklm login           # mở browser → đăng nhập Google
notebooklm auth check --test --json
# Copy toàn bộ JSON output → paste vào Settings
```

**Lưu vào app_config** (table đã có sẵn):

```
key: "notebooklm_auth_json"
value: '{"cookies": [...], ...}'   ← JSON từ auth check
encrypted: true                    ← dùng cryptography.fernet
```

**Settings UI**: Admin Portal → Settings → AI Providers → thêm tab "NotebookLM"

```
┌─────────────────────────────────────────┐
│ NotebookLM                              │
│                                         │
│ Auth JSON:                              │
│ [paste JSON từ notebooklm auth check]   │
│                                         │
│ Auto-delete notebook sau sync: ☑        │
│ Ngôn ngữ report: [vi ▼]                │
│                                         │
│ [Test Connection]  [Save]               │
└─────────────────────────────────────────┘
```

**Endpoint test connection**:
```
GET /api/notebooklm/health
→ { "status": "ok", "authenticated": true }
```

### 1.4 NotebookLM Service

**File**: `app/services/notebooklm_service.py`

```python
"""
Wrapper around the notebooklm-py CLI tool.
Runs CLI commands via subprocess since no Python API is available.
Auth JSON is read from app_config and passed as NOTEBOOKLM_AUTH_JSON env var.
"""

import asyncio
import json
import subprocess
import tempfile
import os
from pathlib import Path
from typing import Optional
from loguru import logger


class NotebookLMError(Exception):
    pass


class NotebookLMService:
    def __init__(self, auth_json: str, language: str = "vi"):
        self.auth_json = auth_json
        self.language = language

    def _env(self) -> dict:
        env = os.environ.copy()
        env["NOTEBOOKLM_AUTH_JSON"] = self.auth_json
        return env

    async def _run(self, args: list[str], timeout: int = 120) -> dict:
        cmd = ["notebooklm"] + args + ["--json"]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._env(),
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            raise NotebookLMError(f"Command timed out: {' '.join(cmd)}")

        if proc.returncode != 0:
            raise NotebookLMError(stderr.decode() or f"Exit code {proc.returncode}")

        try:
            return json.loads(stdout.decode())
        except json.JSONDecodeError as e:
            raise NotebookLMError(f"Invalid JSON response: {e}")

    async def check_auth(self) -> bool:
        try:
            result = await self._run(["auth", "check", "--test"])
            return result.get("status") == "ok" and result.get("checks", {}).get("token_fetch")
        except NotebookLMError:
            return False

    async def create_notebook(self, title: str) -> str:
        result = await self._run(["create", title])
        return result["notebook"]["id"]

    async def add_source_file(self, notebook_id: str, file_path: str) -> str:
        result = await self._run(["source", "add", file_path, "-n", notebook_id], timeout=300)
        return result["source"]["id"]

    async def wait_source_ready(self, source_id: str, notebook_id: str, timeout: int = 300) -> None:
        await self._run(["source", "wait", source_id, "-n", notebook_id, "--timeout", str(timeout)], timeout=timeout + 30)

    async def generate_report(self, notebook_id: str) -> str:
        result = await self._run([
            "generate", "report",
            "--format", "study-guide",
            "--language", self.language,
            "-n", notebook_id,
        ])
        return result["task_id"]

    async def wait_artifact(self, task_id: str, notebook_id: str, timeout: int = 900) -> None:
        await self._run(
            ["artifact", "wait", task_id, "-n", notebook_id, "--timeout", str(timeout)],
            timeout=timeout + 30
        )

    async def download_report(self, notebook_id: str, output_path: str) -> None:
        await self._run(["download", "report", output_path, "-n", notebook_id], timeout=120)

    async def delete_notebook(self, notebook_id: str) -> None:
        await self._run(["delete", notebook_id])
```

### 1.5 Worker Task

**File**: `app/workers/notebooklm_tasks.py`

```python
"""
arq task: sync a source document to NotebookLM and import the generated
report as wiki pages.

Enqueue with:
    await ctx["redis"].enqueue_job("notebooklm_sync_task", source_id=str(source.id))
"""

import asyncio
import tempfile
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.db import AsyncSessionLocal
from app.database.models import Source, WikiPage
from app.services.notebooklm_service import NotebookLMService, NotebookLMError
from app.services.config_service import get_config_value  # reads app_config
from app.services.minio_service import download_file_from_minio
from app.services.wiki_service import upsert_wiki_page_from_markdown


async def notebooklm_sync_task(ctx: dict, source_id: str) -> None:
    logger.info(f"[NLM] Starting sync for source {source_id}")

    async with AsyncSessionLocal() as db:
        source = await db.get(Source, UUID(source_id))
        if not source:
            logger.error(f"[NLM] Source {source_id} not found")
            return

        # Load auth
        auth_json = await get_config_value(db, "notebooklm_auth_json")
        if not auth_json:
            await _fail(db, source, "NotebookLM chưa được cấu hình. Vào Settings → NotebookLM.")
            return

        language = await get_config_value(db, "notebooklm_language") or "vi"
        auto_delete = (await get_config_value(db, "notebooklm_auto_delete")) != "false"

        nlm = NotebookLMService(auth_json=auth_json, language=language)

        # Verify auth before starting
        if not await nlm.check_auth():
            await _fail(db, source, "NotebookLM auth thất bại. Cần login lại.")
            return

        notebook_id = None
        tmp_file = None
        tmp_report = None

        try:
            # 1. Download file from MinIO
            await _set_status(db, source, "uploading")
            with tempfile.NamedTemporaryFile(suffix=Path(source.minio_key).suffix, delete=False) as f:
                tmp_file = f.name
            await download_file_from_minio(source.minio_key, tmp_file)
            logger.info(f"[NLM] Downloaded {source.minio_key} → {tmp_file}")

            # 2. Create notebook
            notebook_id = await nlm.create_notebook(f"Arkon: {source.title}")
            source.notebooklm_notebook_id = notebook_id
            await db.commit()
            logger.info(f"[NLM] Created notebook {notebook_id}")

            # 3. Upload source
            src_id = await nlm.add_source_file(notebook_id, tmp_file)
            await nlm.wait_source_ready(src_id, notebook_id, timeout=300)
            logger.info(f"[NLM] Source ready: {src_id}")

            # 4. Generate report
            await _set_status(db, source, "generating")
            task_id = await nlm.generate_report(notebook_id)
            source.notebooklm_artifact_id = task_id
            await db.commit()
            logger.info(f"[NLM] Generating report, task_id={task_id}")

            # 5. Wait for report (up to 15 min)
            await nlm.wait_artifact(task_id, notebook_id, timeout=900)

            # 6. Download report
            with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as f:
                tmp_report = f.name
            await nlm.download_report(notebook_id, tmp_report)
            logger.info(f"[NLM] Report downloaded: {tmp_report}")

            # 7. Import report into wiki
            report_md = Path(tmp_report).read_text(encoding="utf-8")
            await upsert_wiki_page_from_markdown(
                db=db,
                source=source,
                markdown=report_md,
                tag="nlm-report",
            )
            logger.info(f"[NLM] Wiki pages created/updated for source {source_id}")

            # 8. Mark synced
            source.notebooklm_status = "synced"
            source.notebooklm_error = None
            source.notebooklm_synced_at = datetime.now(timezone.utc)
            await db.commit()

        except NotebookLMError as e:
            logger.error(f"[NLM] Error syncing {source_id}: {e}")
            await _fail(db, source, str(e))

        finally:
            # Cleanup temp files
            for f in [tmp_file, tmp_report]:
                if f and os.path.exists(f):
                    os.unlink(f)
            # Cleanup notebook (optional)
            if auto_delete and notebook_id:
                try:
                    await nlm.delete_notebook(notebook_id)
                    logger.info(f"[NLM] Deleted notebook {notebook_id}")
                except Exception:
                    pass


async def _set_status(db: AsyncSession, source: Source, status: str) -> None:
    source.notebooklm_status = status
    await db.commit()


async def _fail(db: AsyncSession, source: Source, error: str) -> None:
    source.notebooklm_status = "error"
    source.notebooklm_error = error
    await db.commit()
```

### 1.6 Wiki Import từ NLM Report

**File**: `app/services/wiki_service.py` — thêm function:

```python
async def upsert_wiki_page_from_markdown(
    db: AsyncSession,
    source: Source,
    markdown: str,
    tag: str = "nlm-report",
) -> list[WikiPage]:
    """
    Parse a NotebookLM study guide report and create/update wiki pages.
    
    The study guide format is:
        # Title
        ## Section 1
        content...
        ## Section 2
        content...
    
    Each H2 section → một wiki page.
    Tag "nlm-report" giúp phân biệt với MRP-generated pages.
    """
    pages = _split_study_guide(markdown)
    created = []

    for title, content in pages:
        slug = f"nlm/{slugify(title)}"
        existing = await db.execute(
            select(WikiPage).where(WikiPage.slug == slug)
        )
        page = existing.scalar_one_or_none()

        if page:
            page.content_md = content
            page.updated_at = datetime.now(timezone.utc)
        else:
            page = WikiPage(
                slug=slug,
                title=title,
                content_md=content,
                source_id=source.id,
                scope_type=source.scope_type,
                scope_id=source.scope_id,
                knowledge_type_id=source.knowledge_type_id,
                tags=[tag],
                generated_by="notebooklm",
            )
            db.add(page)

        created.append(page)

    await db.commit()
    return created


def _split_study_guide(markdown: str) -> list[tuple[str, str]]:
    """Split NLM study guide by H2 sections."""
    import re
    sections = re.split(r'\n## ', markdown)
    result = []
    for i, section in enumerate(sections):
        if i == 0:
            # Phần đầu là H1 title + intro
            lines = section.strip().split('\n')
            title = lines[0].lstrip('# ').strip() if lines else "Overview"
            content = '\n'.join(lines[1:]).strip()
        else:
            lines = section.strip().split('\n')
            title = lines[0].strip()
            content = '\n'.join(lines[1:]).strip()
        if content:
            result.append((title, content))
    return result
```

### 1.7 API Endpoints

**File**: `app/routers/notebooklm.py`

```python
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.database.db import get_db
from app.services.auth_service import require_permission
from app.services.notebooklm_service import NotebookLMService
from app.services.config_service import get_config_value

router = APIRouter(prefix="/api/notebooklm", tags=["notebooklm"])


@router.get("/health")
async def nlm_health(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission("settings:read")),
):
    """Kiểm tra xem NotebookLM auth có hoạt động không."""
    auth_json = await get_config_value(db, "notebooklm_auth_json")
    if not auth_json:
        return {"status": "not_configured", "authenticated": False}
    nlm = NotebookLMService(auth_json=auth_json)
    authenticated = await nlm.check_auth()
    return {"status": "ok" if authenticated else "auth_failed", "authenticated": authenticated}


@router.post("/sources/{source_id}/sync")
async def trigger_nlm_sync(
    source_id: str,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission("sources:write")),
):
    """Kích hoạt NotebookLM sync thủ công cho một tài liệu."""
    from app.database.models import Source
    from uuid import UUID
    import arq

    source = await db.get(Source, UUID(source_id))
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    if source.notebooklm_status in ("uploading", "generating"):
        raise HTTPException(status_code=409, detail="Sync đang chạy")

    redis = arq.create_pool(...)  # dùng pool từ lifespan
    await redis.enqueue_job("notebooklm_sync_task", source_id=source_id)

    return {"status": "queued", "source_id": source_id}


@router.get("/sources/{source_id}/status")
async def get_nlm_status(
    source_id: str,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission("sources:read")),
):
    from app.database.models import Source
    from uuid import UUID

    source = await db.get(Source, UUID(source_id))
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    return {
        "notebooklm_status": source.notebooklm_status,
        "notebooklm_synced_at": source.notebooklm_synced_at,
        "notebooklm_error": source.notebooklm_error,
    }
```

**Đăng ký router** — `app/main.py`:
```python
from app.routers import notebooklm
app.include_router(notebooklm.router)
```

### 1.8 Frontend: Knowledge Table Action

**File**: `frontend/src/components/knowledge-table.tsx`

Thêm vào dropdown actions (sau "Retry"):

```tsx
{source.status === "ready" && (
  <DropdownMenuItem
    onClick={() => handleNLMSync(source.id)}
    disabled={["uploading", "generating"].includes(source.notebooklm_status ?? "")}
  >
    <NotebooksIcon className="h-4 w-4 mr-2" />
    {source.notebooklm_status === "synced"
      ? "Re-sync to NotebookLM"
      : "Sync to NotebookLM"}
  </DropdownMenuItem>
)}
```

```tsx
const handleNLMSync = async (sourceId: string) => {
  await api.post(`/api/notebooklm/sources/${sourceId}/sync`);
  toast.success("NotebookLM sync đã được xếp hàng");
  refetch();
};
```

**NLM Status Badge** — hiển thị trong cột Status hoặc tooltip:

```tsx
function NLMStatusBadge({ status }: { status?: string }) {
  if (!status) return null;
  const config = {
    pending:    { label: "NLM: Chờ",        color: "gray" },
    uploading:  { label: "NLM: Đang upload", color: "blue" },
    generating: { label: "NLM: Đang tạo",   color: "blue" },
    synced:     { label: "NLM: ✓",          color: "green" },
    error:      { label: "NLM: Lỗi",        color: "red" },
  }[status] ?? { label: status, color: "gray" };

  return <Badge color={config.color}>{config.label}</Badge>;
}
```

---

## Phase 2: Auto-sync khi Upload

Sau khi Phase 1 hoạt động ổn định, thêm option "Auto-sync to NotebookLM" vào upload dialog.

### Upload Dialog

**File**: `frontend/src/components/upload-dialog.tsx`

```tsx
{nlmConfigured && (
  <div className="flex items-center space-x-2">
    <Checkbox
      id="nlm_sync"
      checked={nlmSync}
      onCheckedChange={setNlmSync}
    />
    <Label htmlFor="nlm_sync">
      Đồng bộ lên NotebookLM để tạo tóm tắt chi tiết
    </Label>
  </div>
)}
```

**API endpoint** — `POST /api/sources/upload` thêm field:
```python
class SourceUploadRequest(BaseModel):
    # ... existing fields ...
    notebooklm_sync: bool = False
```

**Sau khi ingest_file_task hoàn thành** — nếu `notebooklm_sync=True`:
```python
# Cuối ingest_file_task
if source.metadata_.get("notebooklm_sync"):
    await ctx["redis"].enqueue_job("notebooklm_sync_task", source_id=str(source.id))
```

### Kiểm tra NLM configured cho Upload Dialog

```
GET /api/notebooklm/health → { "authenticated": true }
```
Frontend chỉ hiển thị checkbox nếu `authenticated: true`.

---

## Phase 3: Podcast / Audio (Tương lai)

Sau khi Phase 2 ổn định, có thể mở rộng sang:

- **Generate podcast**: `notebooklm generate audio` → tạo file `.mp3` → lưu vào MinIO → play trong UI
- **Generate quiz**: `notebooklm generate quiz` → JSON → tạo quiz module trong wiki
- **Generate slide deck**: `notebooklm generate slide-deck --format pptx` → download

Tất cả đều dùng cùng pattern: worker task → CLI call → wait → download → store.

---

## Cấu hình Docker

### docker-compose.yml

```yaml
services:
  worker:
    environment:
      # Không set trực tiếp — đọc từ DB (app_config)
      # NOTEBOOKLM_AUTH_JSON set trong app tại runtime
```

### Cài đặt notebooklm-py trong Docker

**File**: `pyproject.toml` — thêm dependency:
```toml
dependencies = [
    # ... existing ...
    "notebooklm-py[browser]",
]
```

**Dockerfile** (nếu dùng Playwright):
```dockerfile
RUN pip install "notebooklm-py[browser]" && playwright install chromium --with-deps
```

> **Lưu ý**: Playwright chromium thêm ~500MB vào image. Nếu muốn tránh, có thể chạy notebooklm-py ở chế độ không có browser (dùng cookie JSON trực tiếp qua `NOTEBOOKLM_AUTH_JSON`).

---

## Xử lý lỗi và Rate Limits

### Rate limit của NotebookLM

| Hành động | Limit ước tính |
|---|---|
| Tạo notebook | ~10/ngày/account |
| Generate report | ~5-10/ngày/account |
| Audio generation | ~3/ngày/account |

**Xử lý trong worker**:
- Retry tối đa 2 lần với delay 10 phút
- Log rõ ràng "GENERATION_FAILED" → thông báo user

### Session hết hạn

Cookie NotebookLM hết hạn sau ~7-14 ngày. Worker cần:
1. Check auth trước mỗi sync
2. Nếu auth fail → cập nhật source.notebooklm_status = "error", notebooklm_error = "Auth expired. Admin cần login lại NotebookLM."
3. Hiển thị warning trong Admin Portal

### Worker timeout

Report generation có thể mất 15+ phút. arq mặc định timeout = 300s cần tăng:

```python
# WorkerSettings
class WorkerSettings:
    functions = [ingest_file_task, notebooklm_sync_task]
    job_timeout = 1800  # 30 phút cho NLM tasks
```

---

## Kế hoạch triển khai

### Thứ tự implement

```
Tuần 1: Nền tảng
  □ Cài notebooklm-py vào pyproject.toml
  □ Migration 020 (thêm columns)
  □ notebooklm_service.py (CLI wrapper)
  □ config_service: lưu/đọc notebooklm_auth_json
  □ API: GET /api/notebooklm/health

Tuần 2: Worker + Wiki import
  □ notebooklm_tasks.py (worker task)
  □ Đăng ký task vào WorkerSettings
  □ upsert_wiki_page_from_markdown()
  □ API: POST /api/notebooklm/sources/{id}/sync
  □ API: GET /api/notebooklm/sources/{id}/status
  □ Test end-to-end với 1 tài liệu

Tuần 3: Frontend
  □ Knowledge table: dropdown "Sync to NotebookLM"
  □ NLM status badge trong bảng sources
  □ Admin Settings: NLM config panel + Test button
  □ Error display khi sync thất bại

Tuần 4: Auto-sync (Phase 2)
  □ Upload dialog: checkbox "Sync to NotebookLM"
  □ API: thêm notebooklm_sync field vào upload
  □ ingest_file_task: enqueue NLM task sau khi done
  □ Kiểm tra NLM configured → hiển thị/ẩn checkbox

Tuần 5+: Ổn định
  □ Test với nhiều loại tài liệu (PDF, DOCX, URL)
  □ Test rate limit handling
  □ Monitor session expiry
  □ Docs: cập nhật TROUBLESHOOTING.md với NLM errors
```

### Risk Assessment

| Risk | Mức độ | Mitigation |
|---|---|---|
| Cookie expires | Cao | Monitoring + clear error message để admin biết cần re-login |
| Rate limit | Trung bình | Queue-based, không sync đồng thời nhiều tài liệu |
| Report format thay đổi | Thấp | Parser flexible, fallback: store toàn bộ report vào 1 page |
| Image size (+Playwright) | Trung bình | Dùng cookie-only mode (không cần Playwright sau login) |
| NLM API thay đổi | Trung bình | notebooklm-py maintained, update pip package |

---

## Test Plan

### Unit Tests

```python
# test_notebooklm_service.py
async def test_split_study_guide():
    markdown = """# Overview
Intro text.

## Key Concepts
Concept 1...

## Timeline
Event 1...
"""
    result = _split_study_guide(markdown)
    assert len(result) == 3
    assert result[1][0] == "Key Concepts"
```

### Integration Test

```bash
# Chạy manual với tài liệu thật
curl -X POST "http://localhost:5055/api/notebooklm/sources/<source_id>/sync" \
  -H "Authorization: Bearer <token>"

# Theo dõi worker logs
docker compose logs -f worker | grep "\[NLM\]"

# Kiểm tra wiki pages được tạo
curl "http://localhost:5055/api/wiki/pages?tags=nlm-report" \
  -H "Authorization: Bearer <token>"
```

---

## FAQ

**Q: NotebookLM sync có replace wiki pages từ MRP không?**

Không. NLM pages dùng slug prefix `nlm/` riêng biệt. MRP pages dùng `topic/`, `entity/`, `concept/` v.v. Hai nguồn tồn tại song song và bổ sung cho nhau.

**Q: Nếu cookie hết hạn thì sao?**

Tất cả sync tasks sẽ fail với status "error". Admin thấy notification trong portal → chạy `notebooklm login` trên máy host → copy auth JSON mới vào Settings → các task cần chạy lại thủ công.

**Q: Có thể dùng nhiều Google account không?**

Hiện tại thiết kế 1 account cho toàn hệ thống. Nếu cần multi-account, có thể mở rộng sau bằng cách map workspace → account.

**Q: NLM report có được embed (vector search) không?**

Có thể thêm sau. Wiki pages từ NLM (`generated_by="notebooklm"`) có thể được embed giống như MRP pages bằng cách chạy lại embedding pipeline cho các pages có tag `nlm-report`.

**Q: Cần cài đặt gì trên máy chủ?**

Trên máy **host** (một lần duy nhất): `pip install "notebooklm-py[browser]" && playwright install chromium && notebooklm login`.  
Trong Docker container: chỉ cần `pip install notebooklm-py` (không cần browser vì dùng cookie JSON từ env var).
