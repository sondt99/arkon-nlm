"""Storage writes and DB rows must not drift apart (issue #89).

Two failures, both of them "the transaction rolled back but the side effect did not":

  * `create_contribution_from_zip` uploaded entries one at a time with the zip-bomb guards
    interleaved, so a trip at entry 60 raised after 59 objects had already been written. The
    SkillContribution row rolled back and took the only record of the prefix with it, leaving
    those objects in MinIO forever.
  * `upload_skills` committed the skill as `status="processing"` and *then* enqueued the arq
    job. When the enqueue failed the except block deleted the staged ZIP and re-raised, so the
    committed skill sat at "processing" forever — `skills.status` has no error state — with its
    payload gone and its version_hash already advanced, which sends a re-upload of the same
    file down the "metadata_only" path. Manual DB surgery was the only way out.

The commit before dispatch is not the bug and is not changed here: the worker opens its own
session and cannot see rows that are still in this transaction.
"""

import io
import os
import uuid
import zipfile

import pytest
from fastapi import HTTPException

from app.database.models import Employee, Skill, SkillContribution
from app.services import skill_service
from app.services.skill_service import SkillService
from app.services.storage_service import storage_service

USER_ID = uuid.uuid4()


def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, payload in entries.items():
            zf.writestr(name, payload)
    return buf.getvalue()


def _skill_zip(name: str = "alpha") -> bytes:
    return _zip({f"{name}/SKILL.md": f"# {name}\n".encode()})


class _Upload:
    """The subset of UploadFile that these services touch."""

    def __init__(self, filename: str, data: bytes):
        self.filename = filename
        self._data = data

    async def read(self) -> bytes:
        return self._data


class _Pool:
    """arq pool whose enqueue_job can be made to fail from the Nth call onwards."""

    def __init__(self, fail_from: int | None = None):
        self.fail_from = fail_from
        self.jobs: list[tuple] = []

    async def enqueue_job(self, name, *args, **kwargs):
        if self.fail_from is not None and len(self.jobs) >= self.fail_from:
            raise ConnectionError("redis is down")
        self.jobs.append((name, args))
        return object()


@pytest.fixture
def staged(tmp_path, monkeypatch):
    """Stage uploads under tmp_path instead of ./temp_uploads."""
    written: list[str] = []

    def _save(file_data: bytes) -> str:
        path = tmp_path / f"{uuid.uuid4()}.zip"
        path.write_bytes(file_data)
        written.append(str(path))
        return str(path)

    monkeypatch.setattr(SkillService, "_save_temp_zip", staticmethod(_save))
    return written


def _pool(monkeypatch, pool: _Pool) -> _Pool:
    async def _get_pool():
        return pool

    monkeypatch.setattr(skill_service, "get_arq_pool", _get_pool)
    return pool


# --------------------------------------------------------------------------- #
# upload_skills: a failed dispatch must not leave a committed "processing" row
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_a_failed_dispatch_removes_the_skill_it_created(fake_db, staged, monkeypatch):
    _pool(monkeypatch, _Pool(fail_from=0))
    session = fake_db.factory()

    with pytest.raises(HTTPException) as exc:
        await SkillService.upload_skills(
            session, [_Upload("alpha.zip", _skill_zip())],
            department_ids=None, scope_type="global", scope_id=None,
            force=False, current_user_id=USER_ID,
        )

    assert exc.value.status_code == 503
    assert fake_db.table_rows("skills") == [], (
        "a skill nobody can process is still committed at status='processing'"
    )
    assert fake_db.table_rows("skill_versions") == []
    assert not os.path.exists(staged[0]), "the staged payload was left behind"


@pytest.mark.asyncio
async def test_a_failed_dispatch_puts_an_existing_skill_back(fake_db, staged, monkeypatch):
    """The re-upload path: version_hash must be restored or the retry does nothing."""
    existing = fake_db.insert(Skill(
        id=uuid.uuid4(), name="alpha", slug="alpha", status="active",
        current_version=2, version_hash="hash-of-v2", storage_path="skills/x/versions/2/",
    ))
    _pool(monkeypatch, _Pool(fail_from=0))

    with pytest.raises(HTTPException):
        await SkillService.upload_skills(
            fake_db.factory(), [_Upload("alpha.zip", _skill_zip())],
            department_ids=None, scope_type="global", scope_id=None,
            force=True, current_user_id=USER_ID,
        )

    assert existing in fake_db.table_rows("skills")
    assert existing.status == "active", (
        "the skill is stuck at 'processing' — invisible in the UI and not re-uploadable"
    )
    assert existing.version_hash == "hash-of-v2", (
        "version_hash was left advanced, so re-uploading the same ZIP takes the "
        "'metadata_only' path and never re-ingests"
    )
    assert existing.current_version == 2
    assert fake_db.table_rows("skill_versions") == []


@pytest.mark.asyncio
async def test_an_already_dispatched_upload_keeps_its_payload(fake_db, staged, monkeypatch):
    """Second of two files fails: the first job is queued and still needs its ZIP."""
    pool = _pool(monkeypatch, _Pool(fail_from=1))

    with pytest.raises(HTTPException):
        await SkillService.upload_skills(
            fake_db.factory(),
            [_Upload("alpha.zip", _skill_zip("alpha")),
             _Upload("beta.zip", _skill_zip("beta"))],
            department_ids=None, scope_type="global", scope_id=None,
            force=False, current_user_id=USER_ID,
        )

    assert len(pool.jobs) == 1
    dispatched_path = pool.jobs[0][1][2]
    assert os.path.exists(dispatched_path), (
        "the ZIP for a job that IS queued was deleted — the worker will fail to find it"
    )

    remaining = {s.name for s in fake_db.table_rows("skills")}
    assert remaining == {"alpha"}, (
        f"expected only the dispatched skill to survive, got {remaining}"
    )
    undispatched = [p for p in staged if p != dispatched_path]
    assert undispatched and not any(os.path.exists(p) for p in undispatched)


@pytest.mark.asyncio
async def test_a_successful_upload_dispatches_and_keeps_everything(fake_db, staged, monkeypatch):
    pool = _pool(monkeypatch, _Pool())

    results = await SkillService.upload_skills(
        fake_db.factory(), [_Upload("alpha.zip", _skill_zip())],
        department_ids=None, scope_type="global", scope_id=None,
        force=False, current_user_id=USER_ID,
    )

    assert len(results) == 1
    assert len(pool.jobs) == 1
    name, args = pool.jobs[0]
    assert name == "ingest_skill_task"
    skill = fake_db.table_rows("skills")[0]
    version = fake_db.table_rows("skill_versions")[0]
    assert args == (str(skill.id), str(version.id), staged[0], "alpha.zip")
    assert skill.status == "processing"
    assert os.path.exists(staged[0])


@pytest.mark.asyncio
async def test_a_pre_commit_failure_stages_nothing(fake_db, staged, monkeypatch):
    """A duplicate rejection rolls the transaction back, so the payload is garbage."""
    fake_db.insert(Skill(
        id=uuid.uuid4(), name="alpha", slug="alpha", status="active",
        current_version=1, version_hash="whatever",
    ))
    pool = _pool(monkeypatch, _Pool())

    with pytest.raises(HTTPException) as exc:
        await SkillService.upload_skills(
            fake_db.factory(),
            [_Upload("beta.zip", _skill_zip("beta")),
             _Upload("alpha.zip", _skill_zip("alpha"))],
            department_ids=None, scope_type="global", scope_id=None,
            force=False, current_user_id=USER_ID,
        )

    assert exc.value.status_code == 409
    assert pool.jobs == []
    assert not any(os.path.exists(p) for p in staged)


# --------------------------------------------------------------------------- #
# create_contribution_from_zip: no orphaned objects
# --------------------------------------------------------------------------- #

@pytest.fixture
def object_store(monkeypatch):
    """Record uploads and prefix deletions instead of talking to MinIO."""
    uploaded: list[str] = []
    removed: list[str] = []
    fail_at: dict[str, int | None] = {"n": None}

    async def _upload(object_name, data, content_type=None, **_kw):
        if fail_at["n"] is not None and len(uploaded) >= fail_at["n"]:
            raise OSError("MinIO went away")
        uploaded.append(object_name)

    async def _delete_prefix(prefix):
        removed.append(prefix)

    monkeypatch.setattr(storage_service, "upload_file_async", _upload)
    monkeypatch.setattr(storage_service, "delete_prefix_async", _delete_prefix)
    return {"uploaded": uploaded, "removed": removed, "fail_at": fail_at}


def _contributor() -> Employee:
    return Employee(id=USER_ID, email="dev@example.com", name="Dev", role="employee")


def _prefix(fake_db) -> str:
    rows = fake_db.table_rows("skill_contributions")
    assert len(rows) == 1
    return rows[0].storage_path


@pytest.mark.asyncio
async def test_the_file_count_guard_trips_before_any_upload(fake_db, object_store):
    entries = {f"pkg/f{i}.md": b"x" for i in range(101)}

    with pytest.raises(HTTPException) as exc:
        await SkillService.create_contribution_from_zip(
            fake_db.factory(), _Upload("pkg.zip", _zip(entries)), _contributor(),
        )

    assert exc.value.status_code == 400
    assert object_store["uploaded"] == [], (
        f"{len(object_store['uploaded'])} objects were written before the guard tripped, "
        "and the contribution row that named their prefix is being rolled back"
    )
    assert fake_db.commits == 0


@pytest.mark.asyncio
async def test_the_size_guard_trips_before_any_upload(fake_db, object_store):
    big = b"\0" * (4 * 1024 * 1024)
    entries = {f"pkg/f{i}.bin": big for i in range(3)}  # 12 MB declared, cap is 10 MB

    with pytest.raises(HTTPException) as exc:
        await SkillService.create_contribution_from_zip(
            fake_db.factory(), _Upload("pkg.zip", _zip(entries)), _contributor(),
        )

    assert exc.value.status_code == 400
    assert object_store["uploaded"] == []
    assert fake_db.commits == 0


@pytest.mark.asyncio
async def test_an_unsafe_path_uploads_nothing(fake_db, object_store):
    entries = {"pkg/ok.md": b"x", "../escape.md": b"x"}

    with pytest.raises(HTTPException) as exc:
        await SkillService.create_contribution_from_zip(
            fake_db.factory(), _Upload("pkg.zip", _zip(entries)), _contributor(),
        )

    assert exc.value.status_code == 400
    assert object_store["uploaded"] == []


@pytest.mark.asyncio
async def test_a_storage_failure_mid_upload_cleans_up_what_it_wrote(fake_db, object_store):
    object_store["fail_at"]["n"] = 2
    entries = {f"pkg/f{i}.md": b"x" for i in range(5)}

    with pytest.raises(HTTPException) as exc:
        await SkillService.create_contribution_from_zip(
            fake_db.factory(), _Upload("pkg.zip", _zip(entries)), _contributor(),
        )

    assert exc.value.status_code == 500
    assert len(object_store["uploaded"]) == 2
    assert object_store["removed"] == [_prefix(fake_db)], (
        "the objects written before the failure are unreferenced and unfindable"
    )
    assert fake_db.commits == 0


@pytest.mark.asyncio
async def test_a_clean_run_uploads_everything_and_removes_nothing(fake_db, object_store):
    entries = {"pkg/SKILL.md": b"# pkg", "pkg/run.py": b"print(1)"}

    contribution = await SkillService.create_contribution_from_zip(
        fake_db.factory(), _Upload("pkg.zip", _zip(entries)), _contributor(),
    )

    assert isinstance(contribution, SkillContribution)
    assert sorted(object_store["uploaded"]) == [
        f"{contribution.storage_path}pkg/SKILL.md",
        f"{contribution.storage_path}pkg/run.py",
    ]
    assert object_store["removed"] == []
    assert fake_db.commits == 1


@pytest.mark.asyncio
async def test_create_contribution_cleans_up_when_the_commit_fails(
    fake_db, object_store, monkeypatch,
):
    """The same storage-versus-transaction split, in the non-ZIP entry point."""
    session = fake_db.factory()

    async def _boom():
        raise RuntimeError("serialization failure")

    monkeypatch.setattr(session, "commit", _boom)

    with pytest.raises(RuntimeError):
        await SkillService.create_contribution(
            session, skill_id=None, base_version=None, user_id=USER_ID, title="New Thing",
        )

    assert len(object_store["uploaded"]) == 1
    assert object_store["removed"] == [_prefix(fake_db)]


# --------------------------------------------------------------------------- #
# Dead code that carried a live authorization bug
# --------------------------------------------------------------------------- #

def test_bulk_change_scope_is_gone():
    """Dead, and it published department-scoped skills to everyone. See issue #89."""
    assert not hasattr(SkillService, "bulk_change_scope")


def test_no_departments_really_does_mean_visible_to_everyone():
    """Why bulk_change_scope was more than dead weight.

    It wrote scope_type="department" while deleting every SkillDepartment row, and this is the
    clause that then made the skill readable company-wide.
    """
    from sqlalchemy import select

    from app.database.models import SkillDepartment

    stmt = SkillService._apply_skill_filters(
        select(Skill), allowed_department_ids=[uuid.uuid4()],
    )
    sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "NOT (EXISTS" in sql and SkillDepartment.__tablename__ in sql


@pytest.mark.asyncio
async def test_update_skill_clears_the_scope_when_departments_are_removed(fake_db):
    skill = fake_db.insert(Skill(
        id=uuid.uuid4(), name="alpha", slug="alpha", status="active",
        current_version=1, version_hash="h", scope_type="department", scope_id=uuid.uuid4(),
    ))

    updated = await SkillService.update_skill(
        fake_db.factory(), str(skill.id),
        {"scope_type": "department", "scope_id": None, "_explicit_fields": ["scope_type"]},
    )

    assert updated.scope_type == "global", (
        "a skill with no department rows and scope_type='department' is visible to everyone"
    )
    assert updated.scope_id is None


def test_the_versioning_race_still_takes_the_advisory_lock():
    """pg_advisory_xact_lock is this repo's tool for check-then-act; keep it on both paths.

    AST, not a substring: a commented-out call still matches the source text.
    """
    import ast
    import inspect as _inspect
    import textwrap

    def _called_names(func) -> set[str]:
        tree = ast.parse(textwrap.dedent(_inspect.getsource(func)))
        return {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }

    for func in (SkillService._upsert_skill_db_records, SkillService.approve_contribution):
        assert "_lock_skill_for_versioning" in _called_names(func), (
            f"{func.__name__} computes current_version + 1 without serialising on the skill"
        )
