import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import any_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import job_for
from app.auth.deps import AuthContext, current_auth
from app.db import get_session
from app.jobs.context import build_context
from app.models import AddressKey, AuditLog, Candidate, Row, User
from app.review.filters import RowFilter, SortField, encode_cursor, keyset_condition, sort_expr
from app.review.service import BulkAction, RowAction, apply_bulk, apply_row_action, bulk_count

router = APIRouter(prefix="/jobs/{job_id}", tags=["review"])
MAP_LIMIT = 200_000


class RowQuery(BaseModel):
    filter: RowFilter = Field(default_factory=RowFilter)
    sort: SortField = "row_index"
    desc: bool = False
    cursor: str | None = None
    limit: int = Field(default=200, ge=1, le=1000)


def row_out(r: Row, reviewer: str | None = None) -> dict:
    return {
        "id": r.id, "row_index": r.row_index,
        "in_street": r.in_street, "in_house_number": r.in_house_number, "in_city": r.in_city,
        "in_postcode": r.in_postcode, "in_country": r.in_country,
        "m_street": r.m_street, "m_city": r.m_city, "m_postcode": r.m_postcode, "m_country": r.m_country,
        "lat": r.lat, "lon": r.lon, "score": r.score, "band": r.band, "precision": r.precision, "source": r.source,
        "flags": r.flags, "error": r.error, "status": r.status, "version": r.version, "overridden": r.overridden,
        "reviewed_by": reviewer, "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
    }  # fmt: skip


@router.post("/rows/query")
async def query_rows(job_id: uuid.UUID, body: RowQuery, auth: AuthContext = Depends(current_auth),
                     db: AsyncSession = Depends(get_session)):  # fmt: skip
    job = await job_for(db, auth, job_id)
    conds = body.filter.conditions(job.id)
    total = await db.scalar(select(func.count()).where(*conds)) if body.cursor is None else None
    q = select(Row, User.username).outerjoin(User, User.id == Row.reviewed_by).where(*conds)
    if body.cursor:
        try:
            q = q.where(keyset_condition(body.sort, body.desc, body.cursor))
        except (ValueError, TypeError) as e:
            raise HTTPException(422, "Invalid cursor") from e
    expr = sort_expr(body.sort)
    order = [expr.desc(), Row.row_index.desc()] if body.desc else [expr.asc(), Row.row_index.asc()]
    if body.sort == "row_index":
        order = order[:1]
    rows = (await db.execute(q.order_by(*order).limit(body.limit))).all()
    next_cursor = None
    if len(rows) == body.limit:
        last = rows[-1][0]
        value = getattr(last, body.sort)
        if body.sort == "score":
            value = -1 if value is None else value
        elif body.sort != "row_index":
            value = value or ""
        next_cursor = encode_cursor(value, last.row_index)
    return {"rows": [row_out(r, u) for r, u in rows], "next_cursor": next_cursor, "total": total}


@router.get("/rows/{row_id}")
async def row_detail(job_id: uuid.UUID, row_id: int, auth: AuthContext = Depends(current_auth),
                     db: AsyncSession = Depends(get_session)):  # fmt: skip
    job = await job_for(db, auth, job_id)
    row = await db.get(Row, row_id)
    if row is None or row.job_id != job.id:
        raise HTTPException(404, "Row not found")
    key = await db.get(AddressKey, row.address_key_id) if row.address_key_id else None
    cands = (await db.scalars(select(Candidate).where(Candidate.address_key_id == row.address_key_id)
                              .order_by(Candidate.rank))).all() if key else []  # fmt: skip
    history = (await db.scalars(select(AuditLog).where(AuditLog.job_id == job.id, row.id == any_(AuditLog.row_ids))
                                .order_by(AuditLog.at))).all()  # fmt: skip
    reviewer = await db.scalar(select(User.username).where(User.id == row.reviewed_by)) if row.reviewed_by else None
    selected = (row.override or {}).get("candidate_id") if row.overridden else (key.selected_candidate_id if key else None)
    reasons = (row.override or {}).get("reasons") if row.overridden and (row.override or {}).get("type") == "manual" \
        else next((c.reasons for c in cands if c.id == selected), key.reasons if key else [])
    ns = key.norm_state if key else {}
    return {
        "row": row_out(row, reviewer),
        "columns": job.file_meta.get("columns", []),
        "raw": row.raw,
        "normalized": {
            "street": key.n_street if key else None, "house_number": key.n_house_number if key else None,
            "city": key.n_city if key else None, "postcode": key.n_postcode if key else None,
            "country": key.n_country if key else None, "resolution": key.resolution if key else None,
            "country_method": ns.get("country_method"), "postcode_status": ns.get("postcode_status"),
            "postcode_found": ns.get("postcode_found"), "city_match": ns.get("city_match"),
            "city_similarity": ns.get("city_similarity"), "pc_city_distance_km": ns.get("pc_city_distance_km"),
        },
        "corrections": key.corrections if key else [],
        "reasons": reasons or [],
        "ai_reason": key.ai_reason if key else None,
        "override": row.override,
        "candidates": [
            {"id": c.id, "rank": c.rank, "lat": c.lat, "lon": c.lon, "source": c.source, "precision": c.precision,
             "label": c.label, "score": c.score, "band": c.band, "reasons": c.reasons, "flags": c.flags,
             "selected": c.id == selected}
            for c in cands
        ],
        "history": [
            {"at": a.at.isoformat(), "user": a.username, "action": a.action, "count": a.count, "details": a.details}
            for a in history
        ],
    }  # fmt: skip


@router.post("/rows/{row_id}/action")
async def row_action(job_id: uuid.UUID, row_id: int, body: RowAction, auth: AuthContext = Depends(current_auth),
                     db: AsyncSession = Depends(get_session)):  # fmt: skip
    job = await job_for(db, auth, job_id)
    if job.status not in ("ready", "cancelled", "paused") and body.action == "rerun":
        raise HTTPException(409, "Rows can be re-run once processing has stopped")
    row = await db.get(Row, row_id, with_for_update=True)
    if row is None or row.job_id != job.id:
        raise HTTPException(404, "Row not found")
    row = await apply_row_action(db, job, row, auth.user, body, build_context)
    return row_out(row, auth.user.username if row.reviewed_by else None)


@router.post("/bulk/preview")
async def bulk_preview(job_id: uuid.UUID, body: BulkAction, auth: AuthContext = Depends(current_auth),
                       db: AsyncSession = Depends(get_session)):  # fmt: skip
    job = await job_for(db, auth, job_id)
    return {"count": await bulk_count(db, job, body)}


@router.post("/bulk")
async def bulk_apply(job_id: uuid.UUID, body: BulkAction, auth: AuthContext = Depends(current_auth),
                     db: AsyncSession = Depends(get_session)):  # fmt: skip
    job = await job_for(db, auth, job_id)
    return {"updated": await apply_bulk(db, job, auth.user, body)}


@router.post("/map")
async def map_points(job_id: uuid.UUID, body: RowFilter, auth: AuthContext = Depends(current_auth),
                     db: AsyncSession = Depends(get_session)):  # fmt: skip
    job = await job_for(db, auth, job_id)
    res = await db.execute(
        select(Row.id, Row.lat, Row.lon, Row.band).where(body.where(job.id), Row.lat.is_not(None))
        .order_by(Row.row_index).limit(MAP_LIMIT + 1)
    )  # fmt: skip
    pts = [[i, round(lat, 6), round(lon, 6), (band or "?")[0]] for i, lat, lon, band in res]
    return {"points": pts[:MAP_LIMIT], "truncated": len(pts) > MAP_LIMIT}
