import asyncio
import re
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import build_ai_client
from app.ai.mapping import ai_suggest_mapping
from app.api.deps import get_queue, job_for
from app.auth.deps import AuthContext, current_auth, require_admin
from app.config import get_settings
from app.db import get_session
from app.ingest.mapping import TARGET_FIELDS, mapping_problems, required_unmapped, suggest_mapping
from app.ingest.parsing import PREVIEW_ROWS, SUPPORTED_EXTS, ParseError, ParseOptions, parse_file
from app.jobs.context import active_scoring_config
from app.jobs.progress import ACTIVE, band_counts
from app.models import AddressKey, Job, Row, User
from app.normalize.country import ISO2
from app.review.audit import audit
from app.review.rescore import rescore_job
from app.worker import enqueue_job

router = APIRouter(prefix="/jobs", tags=["jobs"])
_SAFE_NAME = re.compile(r"[^\w.\-]+")


class PreviewOptions(BaseModel):
    encoding: str | None = None
    delimiter: str | None = Field(default=None, max_length=1)
    sheet: str | None = None
    has_header: bool | None = None


class StartJob(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    mapping: dict[str, str | None]
    ai_repair: bool = True
    default_country: str | None = None


def job_out(job: Job, owner: str | None = None, summary: dict | None = None) -> dict:
    return {
        "id": str(job.id),
        "name": job.name,
        "owner_id": job.owner_id,
        "owner": owner,
        "status": job.status,
        "message": job.message,
        "file_name": job.file_name,
        "options": job.options,
        "mapping": job.mapping,
        "counts": job.counts,
        "scoring_config_version": job.scoring_config_version,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "file_deleted": job.file_deleted_at is not None,
        **({"summary": summary} if summary is not None else {}),
    }


def _preview(parsed, mapping: dict, mapping_source: dict) -> dict:
    meta = parsed.meta()
    return {
        "file": meta,
        "columns": parsed.columns,
        "rows": parsed.rows[:PREVIEW_ROWS],
        "mapping": mapping,
        "mapping_source": mapping_source,
    }


async def _suggest(parsed) -> tuple[dict, dict]:
    mapping = suggest_mapping(parsed.columns, parsed.rows)
    source = {f: "heuristic" for f, c in mapping.items() if c}
    if required_unmapped(mapping):
        ai = build_ai_client(get_settings())
        if ai is not None:
            ai_map = await ai_suggest_mapping(ai, parsed.columns, parsed.rows)
            for f, col in ai_map.items():
                if col and not mapping.get(f) and col not in mapping.values():
                    mapping[f] = col
                    source[f] = "ai"
    return mapping, source


def _check_rows(parsed) -> None:
    limit = get_settings().max_rows
    if len(parsed.rows) > limit:
        raise HTTPException(422, f"The file has {len(parsed.rows):,} data rows; the limit is {limit:,}.")


@router.post("/upload", status_code=201)
async def upload(file: UploadFile = File(...), auth: AuthContext = Depends(current_auth),
                 db: AsyncSession = Depends(get_session)):  # fmt: skip
    settings = get_settings()
    name = Path(file.filename or "upload").name
    ext = Path(name).suffix.lower()
    if ext not in SUPPORTED_EXTS:
        raise HTTPException(415, f"Unsupported file type. Supported: {', '.join(sorted(SUPPORTED_EXTS))}")
    job_id = uuid.uuid4()
    folder = settings.upload_dir / str(job_id)
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / (_SAFE_NAME.sub("_", name) or f"upload{ext}")
    limit = settings.max_upload_mb * 1024 * 1024
    size = 0
    with dest.open("wb") as out:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > limit:
                out.close()
                shutil.rmtree(folder, ignore_errors=True)
                raise HTTPException(413, f"The file is larger than {settings.max_upload_mb} MB.")
            out.write(chunk)
    try:
        parsed = await asyncio.to_thread(parse_file, dest)
        _check_rows(parsed)
    except ParseError as e:
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(422, f"The file could not be read: {e}") from e
    except HTTPException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    mapping, source = await _suggest(parsed)
    job = Job(id=job_id, owner_id=auth.user.id, name=Path(name).stem[:200], status="draft", file_name=name,
              file_path=str(dest), file_meta=parsed.meta(), mapping=mapping, options={}, counts={})  # fmt: skip
    db.add(job)
    await db.commit()
    return {"job": job_out(job, auth.user.username), "preview": _preview(parsed, mapping, source)}


@router.post("/{job_id}/preview")
async def preview(job_id: uuid.UUID, body: PreviewOptions, auth: AuthContext = Depends(current_auth),
                  db: AsyncSession = Depends(get_session)):  # fmt: skip
    job = await job_for(db, auth, job_id)
    if job.status != "draft" or not job.file_path:
        raise HTTPException(409, "Only draft jobs can be re-parsed")
    opts = ParseOptions(body.encoding, body.delimiter, body.sheet, body.has_header)
    try:
        parsed = await asyncio.to_thread(parse_file, Path(job.file_path), opts)
    except ParseError as e:
        raise HTTPException(422, f"The file could not be read: {e}") from e
    _check_rows(parsed)
    same_columns = parsed.columns == job.file_meta.get("columns")
    mapping, source = (job.mapping, {}) if same_columns else await _suggest(parsed)
    job.file_meta = parsed.meta()
    job.mapping = mapping
    await db.commit()
    return {"job": job_out(job), "preview": _preview(parsed, mapping, source)}


@router.post("/{job_id}/start")
async def start(job_id: uuid.UUID, body: StartJob, auth: AuthContext = Depends(current_auth),
                db: AsyncSession = Depends(get_session)):  # fmt: skip
    job = await job_for(db, auth, job_id, for_update=True)
    if job.status != "draft":
        raise HTTPException(409, "Job has already been started")
    mapping = {f: (body.mapping.get(f) or None) for f in TARGET_FIELDS}
    default_country = (body.default_country or "").strip().upper() or None
    if default_country and default_country not in ISO2:
        raise HTTPException(422, {"problems": [f"{default_country} is not an ISO country code"]})
    problems = mapping_problems(mapping, job.file_meta.get("columns", []), default_country)
    if problems:
        raise HTTPException(422, {"problems": problems})
    settings = get_settings()
    job.name = body.name.strip()
    job.mapping = mapping
    job.options = {"ai_repair": bool(body.ai_repair and settings.ai_available), "ai_requested": body.ai_repair,
                   "default_country": default_country}  # fmt: skip
    job.status = "queued"
    await audit(db, auth.user, "job_start", job_id=job.id, details={"options": job.options, "mapping": mapping})
    await db.commit()
    await enqueue_job(await get_queue(), job.id)
    return job_out(job, auth.user.username)


async def _summaries(db: AsyncSession, job_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict]:
    out: dict[uuid.UUID, dict] = {j: {"bands": {}, "review": {}} for j in job_ids}
    if not job_ids:
        return out
    res = await db.execute(
        select(Row.job_id, Row.band, Row.status, func.count()).where(Row.job_id.in_(job_ids))
        .group_by(Row.job_id, Row.band, Row.status)
    )  # fmt: skip
    for jid, band, status, n in res:
        s = out[jid]
        s["bands"][band or "unscored"] = s["bands"].get(band or "unscored", 0) + n
        s["review"][status] = s["review"].get(status, 0) + n
    return out


@router.get("")
async def list_jobs(auth: AuthContext = Depends(current_auth), db: AsyncSession = Depends(get_session)):
    q = select(Job, User.username).join(User, User.id == Job.owner_id).where(Job.status != "draft")
    if not auth.is_admin:
        q = q.where(Job.owner_id == auth.user.id)
    rows = (await db.execute(q.order_by(Job.created_at.desc()).limit(500))).all()
    summaries = await _summaries(db, [j.id for j, _ in rows])
    return [job_out(j, owner, summaries[j.id]) for j, owner in rows]


@router.get("/{job_id}")
async def get_job(job_id: uuid.UUID, auth: AuthContext = Depends(current_auth), db: AsyncSession = Depends(get_session)):
    job = await job_for(db, auth, job_id)
    owner = await db.scalar(select(User.username).where(User.id == job.owner_id))
    return job_out(job, owner, await band_counts(db, job.id))


@router.post("/{job_id}/cancel")
async def cancel(job_id: uuid.UUID, auth: AuthContext = Depends(current_auth), db: AsyncSession = Depends(get_session)):
    job = await job_for(db, auth, job_id, for_update=True)
    if job.status in ("queued", "paused", "draft") or (job.status == "ai_repair" and job.ai_batch_id):
        job.status, job.message = "cancelled", "cancelled by user"
    elif job.status in ACTIVE:
        job.cancel_requested = True
    else:
        raise HTTPException(409, f"Job is {job.status}")
    await audit(db, auth.user, "job_cancel", job_id=job.id)
    await db.commit()
    return job_out(job)


@router.post("/{job_id}/resume")
async def resume(job_id: uuid.UUID, auth: AuthContext = Depends(current_auth), db: AsyncSession = Depends(get_session)):
    job = await job_for(db, auth, job_id, for_update=True)
    if job.status != "paused":
        raise HTTPException(409, "Only paused jobs can be resumed")
    job.status, job.message, job.cancel_requested = "normalizing", None, False
    await audit(db, auth.user, "job_resume", job_id=job.id)
    await db.commit()
    await enqueue_job(await get_queue(), job.id)
    return job_out(job)


@router.post("/{job_id}/retry-errors")
async def retry_errors(job_id: uuid.UUID, auth: AuthContext = Depends(current_auth),
                       db: AsyncSession = Depends(get_session)):  # fmt: skip
    job = await job_for(db, auth, job_id, for_update=True)
    if job.status not in ("ready", "cancelled"):
        raise HTTPException(409, "Errors can be retried once the job has finished")
    geo = await db.execute(
        update(AddressKey).where(AddressKey.job_id == job.id, AddressKey.error == "geocode_error")
        .values(step="ai_done", error=None, flags=func.array_remove(AddressKey.flags, "geocode_error"))
    )  # fmt: skip
    ai = await db.execute(
        update(AddressKey).where(AddressKey.job_id == job.id, AddressKey.error == "ai_error")
        .values(step="normalized", error=None, flags=func.array_remove(AddressKey.flags, "ai_error"))
    )  # fmt: skip
    keys = geo.rowcount + ai.rowcount
    if not keys:
        raise HTTPException(409, "There are no rows with errors to retry")
    rows = await db.scalar(
        select(func.count()).select_from(Row).join(AddressKey, AddressKey.id == Row.address_key_id)
        .where(Row.job_id == job.id, AddressKey.step != "scored")
    )  # fmt: skip
    job.status, job.message, job.cancel_requested = "normalizing", None, False
    await audit(db, auth.user, "job_retry_errors", job_id=job.id, count=rows)
    await db.commit()
    await enqueue_job(await get_queue(), job.id)
    return {"job": job_out(job), "rows": rows}


@router.delete("/{job_id}", status_code=204)
async def delete_job(job_id: uuid.UUID, auth: AuthContext = Depends(current_auth), db: AsyncSession = Depends(get_session)):
    job = await job_for(db, auth, job_id, for_update=True)
    if job.status in ACTIVE:
        raise HTTPException(409, "Cancel the job before deleting it")
    folder = Path(job.file_path).parent if job.file_path else None
    await audit(db, auth.user, "job_delete", job_id=job.id, details={"name": job.name})
    await db.execute(text("DELETE FROM rows WHERE job_id = :j"), {"j": job.id})
    await db.delete(job)
    await db.commit()
    if folder and folder.exists() and folder.parent == get_settings().upload_dir:
        shutil.rmtree(folder, ignore_errors=True)


@router.post("/{job_id}/rescore")
async def rescore(job_id: uuid.UUID, auth: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_session)):
    job = await job_for(db, auth, job_id, for_update=True)
    if job.status != "ready":
        raise HTTPException(409, "Only finished jobs can be re-scored")
    version, config = await active_scoring_config(db)
    old = job.scoring_config_version
    keys = await rescore_job(db, job, config)
    job.scoring_config_version = version
    job.counts = {**job.counts, **(await band_counts(db, job.id))}
    await audit(db, auth.user, "job_rescore", job_id=job.id, details={"from_version": old, "to_version": version,
                                                                       "keys": keys})  # fmt: skip
    await db.commit()
    return job_out(job)
