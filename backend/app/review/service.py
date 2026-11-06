"""Single-row and bulk review actions. Every action is audited."""

from datetime import UTC, datetime
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs.context import PipelineContext
from app.jobs.pipeline import apply_keys_to_rows, rerun_row
from app.models import AddressKey, Candidate, Job, Row, User
from app.normalize.pipeline import RawAddress
from app.review.audit import audit
from app.review.filters import RowFilter
from app.scoring.engine import Signals, score

SNAPSHOT_FIELDS = ("status", "lat", "lon", "score", "band", "precision", "source", "m_city", "m_postcode", "m_country")


class AddressInput(BaseModel):
    street: str = Field(default="", max_length=300)
    house_number: str = Field(default="", max_length=50)
    city: str = Field(default="", max_length=200)
    postcode: str = Field(default="", max_length=30)
    country: str = Field(default="", max_length=100)


class RowAction(BaseModel):
    action: Literal["confirm", "reject", "reset", "pick_candidate", "manual_pin", "rerun"]
    version: int
    candidate_id: int | None = None
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    input: AddressInput | None = None


class BulkAction(BaseModel):
    action: Literal["confirm", "reject", "reset"]
    filter: RowFilter = Field(default_factory=RowFilter)
    expected_count: int | None = None


def snapshot(row: Row) -> dict:
    return {f: getattr(row, f) for f in SNAPSHOT_FIELDS}


def _bulk_eligibility(action: str):
    if action == "confirm":
        return Row.status == "pending"  # edited rows already count as confirmed; rejected stay rejected
    if action == "reject":
        return Row.status != "rejected"
    return Row.status != "pending"


async def bulk_count(db: AsyncSession, job: Job, body: BulkAction) -> int:
    return await db.scalar(
        select(func.count()).where(body.filter.where(job.id), _bulk_eligibility(body.action))
    ) or 0


async def apply_bulk(db: AsyncSession, job: Job, user: User, body: BulkAction) -> int:
    count = await bulk_count(db, job, body)
    if body.expected_count is not None and count != body.expected_count:
        raise HTTPException(409, {"message": "The number of matching rows has changed; please review again.",
                                  "count": count})  # fmt: skip
    status = {"confirm": "confirmed", "reject": "rejected", "reset": "pending"}[body.action]
    now = datetime.now(UTC)
    reviewed = {"reviewed_by": None, "reviewed_at": None} if status == "pending" else {"reviewed_by": user.id,
                                                                                      "reviewed_at": now}  # fmt: skip
    res = await db.execute(
        update(Row).where(body.filter.where(job.id), _bulk_eligibility(body.action))
        .values(status=status, version=Row.version + 1, **reviewed)
        .execution_options(synchronize_session=False)
    )  # fmt: skip
    await audit(db, user, f"bulk_{body.action}", job_id=job.id, filter=body.filter.model_dump(exclude_defaults=True),
                count=res.rowcount)  # fmt: skip
    await db.commit()
    return res.rowcount


async def apply_row_action(
    db: AsyncSession, job: Job, row: Row, user: User, body: RowAction, ctx_factory
) -> Row:
    if row.version != body.version:
        raise HTTPException(409, {"message": "This row was changed by someone else; showing the current state.",
                                  "version": row.version})  # fmt: skip
    before = snapshot(row)
    now = datetime.now(UTC)
    details: dict = {}

    if body.action in ("confirm", "reject", "reset"):
        row.status = {"confirm": "confirmed", "reject": "rejected", "reset": "pending"}[body.action]
    elif body.action == "pick_candidate":
        cand = await db.get(Candidate, body.candidate_id) if body.candidate_id else None
        if cand is None or cand.address_key_id != row.address_key_id:
            raise HTTPException(422, "Unknown candidate for this row")
        key = await db.get(AddressKey, row.address_key_id)
        if key is not None and cand.id == key.selected_candidate_id:
            row.overridden, row.override = False, None
            await db.flush()
            await apply_keys_to_rows(db, [key.id])
            await db.refresh(row)
        else:
            comps = cand.components or {}
            row.overridden = True
            row.override = {"type": "candidate", "candidate_id": cand.id, "by": user.username, "previous": before}
            row.lat, row.lon, row.precision, row.source = cand.lat, cand.lon, cand.precision, cand.source
            row.score, row.band = cand.score, cand.band
            row.m_city = comps.get("city") or comps.get("town") or comps.get("village") or row.m_city
            row.m_postcode = comps.get("postcode") or row.m_postcode
            row.flags = list(dict.fromkeys([*(f for f in row.flags if f != "manual"), "candidate_picked"]))
        row.status = "edited"
        details["candidate_id"] = cand.id
    elif body.action == "manual_pin":
        if body.lat is None or body.lon is None:
            raise HTTPException(422, "lat and lon are required")
        result = score(Signals(manual_by=user.username, precision="manual"))
        row.overridden = True
        row.override = {"type": "manual", "by": user.username, "at": now.isoformat(), "previous": before,
                        "reasons": result.reasons}  # fmt: skip
        row.lat, row.lon, row.precision, row.source = body.lat, body.lon, "manual", "manual"
        row.score, row.band = result.score, result.band
        row.flags = list(dict.fromkeys([*(f for f in row.flags if f != "candidate_picked"), "manual"]))
        row.status = "edited"
    elif body.action == "rerun":
        if body.input is None:
            raise HTTPException(422, "input is required")
        ctx: PipelineContext = ctx_factory()
        await ctx.gazetteer.refresh_version()
        inp = RawAddress(**body.input.model_dump())
        key = await rerun_row(db, job, inp, ctx)
        row.address_key_id = key.id
        row.in_street, row.in_house_number, row.in_city = inp.street, inp.house_number, inp.city
        row.in_postcode, row.in_country = inp.postcode, inp.country
        row.overridden, row.override = False, None
        await db.flush()
        await apply_keys_to_rows(db, [key.id])
        await db.refresh(row)
        row.status = "edited"
        details["input"] = body.input.model_dump()

    if row.status == "pending":
        row.reviewed_by, row.reviewed_at = None, None
    else:
        row.reviewed_by, row.reviewed_at = user.id, now
    row.version += 1
    await audit(db, user, f"row_{body.action}", job_id=job.id, row_ids=[row.id], count=1,
                details={**details, "before": before, "after": snapshot(row)})  # fmt: skip
    await db.commit()
    return row
