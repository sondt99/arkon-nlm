import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
)
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.database.models import (
    Employee,
    Skill,
    SkillContribution,
    SkillContributionStatus,
    SkillVersion,
)
from app.routers.skills import is_text_file
from app.services.auth_service import (
    get_current_user,
    require_permission,
)
from app.services.permission_engine import _get_user_permissions
from app.services.skill_scope import (
    ensure_reviewer_can_approve,
    ensure_scope_change_allowed,
    normalize_contribution_scope,
)
from app.services.skill_service import SkillService
from app.services.storage_service import safe_relative_path, storage_service

router = APIRouter()


def _safe_path(path: str) -> str:
    """Sanitize a user-supplied contribution file path or raise 400."""
    try:
        return safe_relative_path(path)
    except ValueError:
        raise HTTPException(400, "Invalid file path")


async def _contribution_objects(contribution: SkillContribution) -> list:
    """List a contribution's stored objects once per write.

    Both the root-folder detection and the workspace budget need this listing, and it is a
    paginated round-trip to MinIO, so fetching it twice per saved file doubled the latency
    of every save for no gain.
    """
    return await storage_service.list_objects_async(
        contribution.storage_path, recursive=True
    )


def _assert_contribution_budget(objects: list, full_path: str, new_bytes: int) -> None:
    """Reject a write that would push a contribution past its cumulative budget.

    Per-file caps bound one request, not the workspace: a contributor could repeat an
    under-cap upload, or PUT an under-cap text file under a fresh path, until MinIO filled.
    The ZIP entry point caps a whole bundle at 10 MB uncompressed, so the incremental
    paths have to share a budget of their own or they simply route around it.
    """
    limit = settings.max_contribution_total_mb * 1024 * 1024
    stored = 0
    replaced = 0
    is_replacement = False
    for obj in objects:
        size = getattr(obj, "size", 0) or 0
        stored += size
        if obj.object_name == full_path:
            # Overwriting an existing file frees its bytes, so charging for both would
            # make a workspace unfixable once it neared the limit.
            is_replacement = True
            replaced = size

    projected = stored - replaced + new_bytes
    if projected > limit:
        raise HTTPException(
            413,
            f"Contribution would hold {projected // (1024 * 1024)} MB, over the "
            f"{settings.max_contribution_total_mb} MB limit for one contribution.",
        )

    if not is_replacement and len(objects) >= settings.max_contribution_files:
        raise HTTPException(
            400,
            f"Contribution already holds {len(objects)} files; maximum allowed is "
            f"{settings.max_contribution_files}.",
        )


async def _prefixed_contribution_path(
    db: AsyncSession, contribution: SkillContribution, file_path: str, objects: list
) -> str:
    """Prefix a sanitized path with the contribution's detected root folder.

    Extracted because the upload and PUT handlers carried byte-identical copies of this
    block, so the storage listing behind it had to be moved off the event loop in two
    places at once and could drift apart afterwards. `objects` is passed in rather than
    listed here so one write costs one listing — the budget check needs the same data.
    """
    existing_files = [
        obj.object_name.replace(contribution.storage_path, "", 1) for obj in objects
    ]

    # Take the first segment of the first file as the root
    root_folder = existing_files[0].split("/")[0] if existing_files else ""

    if not root_folder and contribution.skill_id:
        # Fallback to skill slug if no files exist yet
        skill = await db.get(Skill, contribution.skill_id)
        if skill:
            root_folder = skill.slug

    if not root_folder:
        from app.utils.text import slugify
        root_folder = slugify(contribution.title.replace("Upload: ", ""))

    if root_folder and not file_path.startswith(f"{root_folder}/"):
        file_path = f"{root_folder}/{file_path.lstrip('/')}"

    return file_path

class SkillContributionCreate(BaseModel):
    skill_id: Optional[uuid.UUID] = None
    base_version: Optional[int] = None
    title: str
    scope_type: str = "global"
    scope_ids: Optional[List[uuid.UUID]] = None

class SkillContributionResponse(BaseModel):
    id: uuid.UUID
    skill_id: Optional[uuid.UUID]
    contributor_id: uuid.UUID
    base_version: Optional[int]
    status: str
    title: str
    storage_path: Optional[str]
    scope_type: str
    scope_ids: Optional[List[uuid.UUID]]
    contributor_name: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    
    model_config = {"from_attributes": True}

class PutFileRequest(BaseModel):
    path: str
    content: str

class SkillContributionApprove(BaseModel):
    final_scope_type: Optional[str] = None
    final_scope_ids: Optional[List[uuid.UUID]] = None

# --- Routes ---

@router.get("/skill-contributions/check")
async def check_existing_draft(
    title: str = Query(...),
    skill_id: Optional[uuid.UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
    user: Employee = Depends(get_current_user),
):
    """Check if the user already has a draft for this skill/title."""
    stmt = select(SkillContribution).where(
        SkillContribution.contributor_id == user.id,
        SkillContribution.status == SkillContributionStatus.DRAFT.value
    )
    if skill_id:
        stmt = stmt.where(SkillContribution.skill_id == skill_id)
    else:
        stmt = stmt.where(SkillContribution.title == title)
        
    res = await db.execute(stmt)
    draft = res.scalars().first()
    return draft

@router.post("/skill-contributions", response_model=SkillContributionResponse)
async def create_skill_contribution(
    req: SkillContributionCreate,
    db: AsyncSession = Depends(get_db),
    user: Employee = Depends(get_current_user),
):
    """Start a new skill contribution (fork from a version or create new)."""
    perms = _get_user_permissions(user)
    if user.role != "admin" and "skill:create:all" not in perms:
        if "skill:create:own_dept" not in perms:
            raise HTTPException(403, "Permission required: skill:create")

    base_skill = None
    if req.skill_id:
        base_skill = await db.get(Skill, req.skill_id)
        if base_skill:
            if base_skill.is_system:
                raise HTTPException(403, "System skills cannot be modified via contributions")
            from app.services.permission_engine import can_access_skill
            if not await can_access_skill(db, user, base_skill, "read"):
                raise HTTPException(403, "You do not have access to this skill")

    scope_type, scope_ids = normalize_contribution_scope(
        req.scope_type, req.scope_ids, skill=base_skill,
    )
    if scope_type == "department" and scope_ids and base_skill is None:
        from app.database.models import Department
        for dept_id in scope_ids:
            if await db.get(Department, dept_id) is None:
                raise HTTPException(400, f"Department not found: {dept_id}")

    contribution = await SkillService.create_contribution(
        db, req.skill_id, req.base_version, user.id, req.title, scope_type, scope_ids
    )
    return contribution

@router.get("/admin/skill-contributions", response_model=List[SkillContributionResponse])
async def list_pending_skill_contributions(
    skill_id: Optional[uuid.UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
    admin: Employee = require_permission("skill:contribution:review"),
):
    """List skill contribution requests for admin review."""
    from sqlalchemy import select
    from sqlalchemy.orm import joinedload
    
    stmt = select(SkillContribution).options(joinedload(SkillContribution.contributor))
    stmt = stmt.where(SkillContribution.status == SkillContributionStatus.PENDING.value)
    if skill_id:
        stmt = stmt.where(SkillContribution.skill_id == skill_id)
    stmt = stmt.order_by(SkillContribution.created_at.desc())
    res = await db.execute(stmt)
    contributions = res.scalars().unique().all()
    
    results = []
    for c in contributions:
        resp = SkillContributionResponse.model_validate(c)
        resp.contributor_name = c.contributor.name if c.contributor else "Unknown"
        results.append(resp)
        
    return results

@router.get("/skill-contributions", response_model=List[SkillContributionResponse])
async def list_skill_contributions(
    status: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    user: Employee = Depends(get_current_user),
):
    """List only the current user's skill contributions."""
    from sqlalchemy import select
    from sqlalchemy.orm import joinedload
    
    stmt = select(SkillContribution).options(joinedload(SkillContribution.contributor))
    
    if status:
        stmt = stmt.where(SkillContribution.status == status)
    
    # Always filter by current user's ID (even for admins in this personal view)
    stmt = stmt.where(SkillContribution.contributor_id == user.id)
    
    stmt = stmt.order_by(SkillContribution.created_at.desc())
    res = await db.execute(stmt)
    contributions = res.scalars().unique().all()
    
    results = []
    for c in contributions:
        resp = SkillContributionResponse.model_validate(c)
        resp.contributor_name = c.contributor.name if c.contributor else "Unknown"
        results.append(resp)
        
    return results

@router.get("/skill-contributions/{contribution_id}", response_model=SkillContributionResponse)
async def get_skill_contribution(
    contribution_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: Employee = Depends(get_current_user),
):
    """Get details of a specific skill contribution."""
    from sqlalchemy.orm import joinedload
    contribution = await db.get(
        SkillContribution, 
        contribution_id,
        options=[joinedload(SkillContribution.contributor)]
    )
    if not contribution:
        raise HTTPException(404, "Contribution not found")
    
    if user.role != "admin" and str(contribution.contributor_id) != str(user.id) and "skill:contribution:review" not in _get_user_permissions(user):
        logger.warning(f"[Auth] Access Denied for contribution {contribution_id}. Contributor: {contribution.contributor_id}, User: {user.id}")
        raise HTTPException(403, "Access denied")
    
    resp = SkillContributionResponse.model_validate(contribution)
    resp.contributor_name = contribution.contributor.name if contribution.contributor else "Unknown"
    return resp

@router.delete("/skill-contributions/{contribution_id}")
async def delete_skill_contribution(
    contribution_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: Employee = Depends(get_current_user),
):
    """Delete a skill contribution request and its storage."""
    contribution = await db.get(SkillContribution, contribution_id)
    if not contribution:
        raise HTTPException(404, "Contribution not found")
    
    if user.role != "admin" and str(contribution.contributor_id) != str(user.id):
        raise HTTPException(403, "Access denied")
    
    if user.role != "admin" and contribution.status == SkillContributionStatus.APPROVED.value:
        raise HTTPException(400, "Cannot delete an approved contribution")

    if contribution.storage_path:
        # delete_prefix lists then removes every object one at a time, so a contribution
        # with hundreds of files would otherwise hold the loop for the whole sweep.
        await storage_service.delete_prefix_async(contribution.storage_path)

    await db.delete(contribution)
    await db.commit()
    return {"status": "ok"}

@router.get("/skill-contributions/{contribution_id}/files")
async def list_skill_contribution_files(
    contribution_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: Employee = Depends(get_current_user),
):
    """List files in the temporary storage of a skill contribution."""
    contribution = await db.get(SkillContribution, contribution_id)
    if not contribution:
        raise HTTPException(404, "Contribution not found")
    
    if user.role != "admin" and str(contribution.contributor_id) != str(user.id) and "skill:contribution:review" not in _get_user_permissions(user):
        raise HTTPException(403, "Access denied")

    if contribution.status == SkillContributionStatus.APPROVED.value:
        raise HTTPException(400, f"Cannot access contribution files in status: {contribution.status}")
    
    prefix = contribution.storage_path
    if not prefix:
        return []

    objects = await storage_service.list_objects_async(prefix, recursive=True)

    files = []
    for obj in objects:
        rel_path = obj.object_name.replace(prefix, "", 1)
        if not rel_path: 
            continue
        files.append({
            "path": rel_path,
            "size": obj.size,
            "is_text": is_text_file(rel_path)
        })
    return sorted(files, key=lambda x: x["path"])

@router.get("/skill-contributions/{contribution_id}/files/content")
async def get_skill_contribution_file_content(
    contribution_id: uuid.UUID,
    path: str,
    db: AsyncSession = Depends(get_db),
    user: Employee = Depends(get_current_user),
):
    """Read content of a file in a skill contribution."""
    contribution = await db.get(SkillContribution, contribution_id)
    if not contribution:
        raise HTTPException(404, "Contribution not found")
    
    if user.role != "admin" and str(contribution.contributor_id) != str(user.id) and "skill:contribution:review" not in _get_user_permissions(user):
        raise HTTPException(403, "Access denied")
        
    full_path = f"{contribution.storage_path}{_safe_path(path)}"
    try:
        content_bytes = await storage_service.download_file_async(full_path)
        return {"content": content_bytes.decode("utf-8", errors="ignore")}
    except Exception as e:
        logger.error(f"Failed to read contribution file {full_path}: {e}")
        raise HTTPException(500, "Failed to read file")

@router.post("/skill-contributions/{contribution_id}/rename")
async def rename_skill_contribution_file(
    contribution_id: uuid.UUID,
    old_path: str = Query(...),
    new_path: str = Query(...),
    db: AsyncSession = Depends(get_db),
    user: Employee = Depends(get_current_user),
):
    """Rename a file or folder in a skill contribution."""
    contribution = await db.get(SkillContribution, contribution_id)
    if not contribution:
        raise HTTPException(404, "Contribution not found")
    
    if user.role != "admin" and str(contribution.contributor_id) != str(user.id):
        raise HTTPException(403, "Access denied")
    
    # Only a DRAFT is editable. Guarding on APPROVED alone left PENDING writable, so a
    # contributor could swap file content in the window between a reviewer reading the
    # diff and clicking approve — shipping unreviewed content attributed to the reviewer,
    # with the recorded version_hash covering the swapped bytes.
    if contribution.status != SkillContributionStatus.DRAFT.value:
        raise HTTPException(
            400,
            f"Cannot edit contribution in status: {contribution.status}. "
            "Only draft contributions are editable.",
        )

    # Guards run on the NORMALIZED paths — checking the raw input would let
    # shapes like "root/SKILL.md/." slip past endswith after normalization.
    old_path = _safe_path(old_path)
    new_path = _safe_path(new_path)

    parts = old_path.split("/")
    if len(parts) == 1:
        raise HTTPException(400, "Cannot rename the root folder.")

    if old_path.endswith("SKILL.md"):
        raise HTTPException(400, "Cannot rename SKILL.md.")

    full_old_path = f"{contribution.storage_path}{old_path}"
    full_new_path = f"{contribution.storage_path}{new_path}"
    
    if contribution.status == SkillContributionStatus.PENDING.value:
        contribution.status = SkillContributionStatus.DRAFT.value

    await storage_service.move_prefix_async(full_old_path, full_new_path)
    await db.commit()
    return {"status": "ok", "old_path": old_path, "new_path": new_path, "contribution_status": contribution.status}

@router.delete("/skill-contributions/{contribution_id}/files")
async def delete_skill_contribution_file(
    contribution_id: uuid.UUID,
    path: str,
    db: AsyncSession = Depends(get_db),
    user: Employee = Depends(get_current_user),
):
    """Delete a file or folder (prefix) from a skill contribution."""
    contribution = await db.get(SkillContribution, contribution_id)
    if not contribution:
        raise HTTPException(404, "Contribution not found")
    
    if user.role != "admin" and str(contribution.contributor_id) != str(user.id):
        raise HTTPException(403, "Access denied")
    
    # Only a DRAFT is editable. Guarding on APPROVED alone left PENDING writable, so a
    # contributor could swap file content in the window between a reviewer reading the
    # diff and clicking approve — shipping unreviewed content attributed to the reviewer,
    # with the recorded version_hash covering the swapped bytes.
    if contribution.status != SkillContributionStatus.DRAFT.value:
        raise HTTPException(
            400,
            f"Cannot edit contribution in status: {contribution.status}. "
            "Only draft contributions are editable.",
        )

    full_path = f"{contribution.storage_path}{_safe_path(path)}"

    if contribution.status == SkillContributionStatus.PENDING.value:
        contribution.status = SkillContributionStatus.DRAFT.value

    await storage_service.delete_prefix_async(full_path)
    await db.commit()
    return {"status": "ok", "path": path, "contribution_status": contribution.status}

@router.post("/skill-contributions/{contribution_id}/upload")
async def upload_skill_contribution_file(
    contribution_id: uuid.UUID,
    file: UploadFile = File(...),
    path: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """Upload a binary file to a skill contribution."""
    contribution = await db.get(SkillContribution, contribution_id)
    if not contribution:
        raise HTTPException(404, "Contribution not found")
    
    if current_user.role != "admin" and str(contribution.contributor_id) != str(current_user.id):
        raise HTTPException(403, "Access denied")
    
    # Only a DRAFT is editable. Guarding on APPROVED alone left PENDING writable, so a
    # contributor could swap file content in the window between a reviewer reading the
    # diff and clicking approve — shipping unreviewed content attributed to the reviewer,
    # with the recorded version_hash covering the swapped bytes.
    if contribution.status != SkillContributionStatus.DRAFT.value:
        raise HTTPException(
            400,
            f"Cannot edit contribution in status: {contribution.status}. "
            "Only draft contributions are editable.",
        )

    objects = await _contribution_objects(contribution)
    file_path = await _prefixed_contribution_path(
        db, contribution, _safe_path(path if path else file.filename), objects
    )
    full_path = f"{contribution.storage_path}{file_path.lstrip('/')}"

    from app.services.upload_guard import content_type_from_extension, spooled_upload

    # file.content_type was persisted verbatim, so a contributor could store `text/html`
    # against an uploaded asset. The stored type is now derived from the extension, the way
    # zip_service already does it for archive entries.
    content_type = content_type_from_extension(file_path)
    # Guards run outside the try: inside it, the 413 was caught by `except Exception` and
    # reported as a 500, which hid the limit from the client entirely.
    stream, length = await spooled_upload(file, what="File")
    _assert_contribution_budget(objects, full_path, length)

    try:
        if contribution.status == SkillContributionStatus.PENDING.value:
            contribution.status = SkillContributionStatus.DRAFT.value

        await storage_service.upload_stream_async(
            full_path, stream, length, content_type=content_type
        )
        await db.commit()
        return {"status": "ok", "path": file_path, "contribution_status": contribution.status}
    except Exception as e:
        logger.error(f"Failed to upload to MinIO: {str(e)}")
        raise HTTPException(500, "File upload failed")

@router.put("/skill-contributions/{contribution_id}/files")
async def put_skill_contribution_file(
    contribution_id: uuid.UUID,
    request: PutFileRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """Create or update a text file in a skill contribution."""
    contribution = await db.get(SkillContribution, contribution_id)
    if not contribution:
        raise HTTPException(404, "Contribution not found")
    
    if current_user.role != "admin" and str(contribution.contributor_id) != str(current_user.id):
        raise HTTPException(403, "Access denied")

    # Only a DRAFT is editable. Guarding on APPROVED alone left PENDING writable, so a
    # contributor could swap file content in the window between a reviewer reading the
    # diff and clicking approve — shipping unreviewed content attributed to the reviewer,
    # with the recorded version_hash covering the swapped bytes.
    if contribution.status != SkillContributionStatus.DRAFT.value:
        raise HTTPException(
            400,
            f"Cannot edit contribution in status: {contribution.status}. "
            "Only draft contributions are editable.",
        )
    
    # `content` is a JSON string, so none of the multipart guards apply to it and this was
    # the one write path with no byte cap at all. Checked before the storage listing below
    # so an oversized body costs no round-trip.
    content_bytes = request.content.encode("utf-8")
    text_limit = settings.max_contribution_text_kb * 1024
    if len(content_bytes) > text_limit:
        raise HTTPException(
            413,
            f"File content is {len(content_bytes) // 1024} KB, over the "
            f"{settings.max_contribution_text_kb} KB limit.",
        )

    objects = await _contribution_objects(contribution)
    file_path = await _prefixed_contribution_path(
        db, contribution, _safe_path(request.path), objects
    )
    full_path = f"{contribution.storage_path}{file_path.lstrip('/')}"
    _assert_contribution_budget(objects, full_path, len(content_bytes))

    if contribution.status == SkillContributionStatus.PENDING.value:
        contribution.status = SkillContributionStatus.DRAFT.value

    await storage_service.upload_file_async(
        full_path, content_bytes, content_type="text/plain"
    )
    await db.commit()
    return {"status": "ok", "path": file_path, "contribution_status": contribution.status}

@router.post("/skill-contributions/{contribution_id}/submit")
async def submit_skill_contribution(
    contribution_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: Employee = Depends(get_current_user),
):
    """Submit skill contribution for review."""
    contribution = await db.get(SkillContribution, contribution_id)
    if not contribution:
        raise HTTPException(404, "Contribution not found")

    if contribution.contributor_id != user.id and user.role != "admin":
        raise HTTPException(403, "You can only submit your own contributions")

    if contribution.status == SkillContributionStatus.APPROVED.value:
        raise HTTPException(400, "Contribution already approved")

    contribution = await SkillService.submit_contribution(db, contribution_id)
    return {"status": contribution.status}

@router.post("/skill-contributions/{contribution_id}/approve")
async def approve_skill_contribution(
    contribution_id: uuid.UUID,
    req: Optional[SkillContributionApprove] = None,
    db: AsyncSession = Depends(get_db),
    admin: Employee = require_permission("skill:contribution:review"),
):
    """
    Approve and merge skill contribution into the main skill.
    Enforces department-level review for non-admin users if scope is 'department'.
    """
    contribution = await db.get(SkillContribution, contribution_id)
    if not contribution:
        raise HTTPException(404, "Contribution not found")
    if contribution.status in [SkillContributionStatus.APPROVED.value, SkillContributionStatus.REJECTED.value]:
        raise HTTPException(400, "Contribution already reviewed")

    skill = contribution.skill
    if skill is None and contribution.skill_id:
        skill = await db.get(Skill, contribution.skill_id)
    ensure_reviewer_can_approve(admin, skill, contribution)

    final_scope_type = req.final_scope_type if req else None
    final_scope_ids = req.final_scope_ids if req else None
    ensure_scope_change_allowed(admin, final_scope_type)

    skill = await SkillService.approve_contribution(
        db, 
        contribution_id, 
        admin.id,
        final_scope_type=final_scope_type,
        final_scope_ids=final_scope_ids
    )
    return {
        "status": "approved", 
        "skill_id": skill.id, 
        "skill_slug": skill.slug,
        "version": skill.current_version
    }

@router.post("/skill-contributions/{contribution_id}/reject")
async def reject_skill_contribution(
    contribution_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: Employee = require_permission("skill:contribution:review"),
):
    """Reject a skill contribution request (moves back to draft)."""
    contribution = await db.get(SkillContribution, contribution_id)
    if not contribution:
        raise HTTPException(404, "Contribution not found")
    if contribution.status in [SkillContributionStatus.APPROVED.value, SkillContributionStatus.REJECTED.value]:
        raise HTTPException(400, "Contribution already reviewed")

    contribution = await SkillService.reject_contribution(db, contribution_id)
    return {"status": contribution.status}

@router.get("/skill-contributions/{contribution_id}/diff-status")
async def get_skill_contribution_diff_status(
    contribution_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: Employee = Depends(get_current_user),
):
    """
    Returns a map of {path: status} where status is 'A' (Added), 'D' (Deleted), or 'M' (Modified).
    """
    
    contribution = await db.get(SkillContribution, contribution_id)
    if not contribution:
        raise HTTPException(404, "Contribution not found")
    
    if user.role != "admin" and str(contribution.contributor_id) != str(user.id) and "skill:contribution:review" not in _get_user_permissions(user):
        raise HTTPException(403, "Access denied")
        
    contrib_files = await storage_service.list_objects_async(
        contribution.storage_path, recursive=True
    )
    contrib_map = {f.object_name.replace(contribution.storage_path, "", 1).lstrip("/"): f for f in contrib_files}
    
    base_files_map = {}
    if contribution.skill_id:
        # User requested to ALWAYS compare with latest version
        stmt = select(SkillVersion).where(SkillVersion.skill_id == contribution.skill_id).order_by(SkillVersion.version_number.desc())
        v_res = await db.execute(stmt)
        v_obj = v_res.scalars().first()
        
        if v_obj:
            base_prefix = v_obj.storage_path
            base_files = await storage_service.list_objects_async(
                base_prefix, recursive=True
            )

            for f in base_files:
                rel = f.object_name.replace(base_prefix, "", 1).lstrip("/")
                # Handle 'content/' prefix if present in original storage
                if rel.startswith("content/"):
                    rel = rel.replace("content/", "", 1).lstrip("/")
                
                if rel:
                    base_files_map[rel] = f

    # Determine if there's a root folder shift (e.g. contrib has 'slug/' but base doesn't, or vice-versa)
    contrib_paths = list(contrib_map.keys())
    base_paths = list(base_files_map.keys())
    
    # Simple check for common root folder in contrib
    contrib_root = ""
    if contrib_paths:
        first = contrib_paths[0]
        if "/" in first:
            root = first.split("/")[0]
            if all(p.startswith(root + "/") or p == root for p in contrib_paths):
                contrib_root = root

    # Simple check for common root folder in base
    base_root = ""
    if base_paths:
        first = base_paths[0]
        if "/" in first:
            root = first.split("/")[0]
            if all(p.startswith(root + "/") or p == root for p in base_paths):
                base_root = root

    status_map = {}
    
    # If roots match (both have it or both don't), simple comparison
    # If roots differ, we should try to match paths by stripping/adding roots
    
    def get_norm(p, root):
        if root and (p.startswith(root + "/") or p == root):
            return p[len(root):].lstrip("/")
        return p

    # Map normalized paths back to actual paths for each side
    norm_to_contrib = {get_norm(p, contrib_root): p for p in contrib_paths}
    norm_to_base = {get_norm(p, base_root): p for p in base_paths}
    
    all_norms = set(norm_to_contrib.keys()) | set(norm_to_base.keys())
    
    for norm in all_norms:
        if not norm: 
            continue
        
        p_contrib = norm_to_contrib.get(norm)
        p_base = norm_to_base.get(norm)
        
        if p_contrib and not p_base:
            status_map[p_contrib] = "A"
        elif p_base and not p_contrib:
            # For deleted files, we prefer the path WITH the contrib root if it exists
            # so it shows up in the same folder in the UI
            target_path = f"{contrib_root}/{norm}" if contrib_root else norm
            status_map[target_path] = "D"
        else:
            # Both exist
            c_file = contrib_map[p_contrib]
            b_file = base_files_map[p_base]
            if c_file.size != b_file.size or c_file.etag != b_file.etag:
                status_map[p_contrib] = "M"
                
    return status_map
