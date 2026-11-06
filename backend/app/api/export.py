import json
import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import job_for
from app.auth.deps import AuthContext, current_auth
from app.db import get_session, get_sessionmaker
from app.export.csv_export import BOM, csv_lines, export_row, filename, header
from app.models import AddressKey, Row, User
from app.review.audit import audit
from app.review.filters import RowFilter

router = APIRouter(prefix="/jobs/{job_id}", tags=["export"])
Scope = Literal["confirmed", "all", "current_filter"]
BATCH = 2000


def _scope_filter(scope: Scope, filter_json: str | None) -> RowFilter:
    if scope == "current_filter":
        try:
            return RowFilter.model_validate(json.loads(filter_json or "{}"))
        except (ValueError, ValidationError) as e:
            raise HTTPException(422, "Invalid filter") from e
    if scope == "confirmed":
        return RowFilter(statuses=["confirmed", "edited"])
    return RowFilter()


def _corrections(key_corrections: list | None, overridden: dict | None, flags: list[str]) -> str:
    parts = [c.get("sentence", "") for c in (key_corrections or [])]
    if overridden and overridden.get("type") == "manual":
        parts.append(f"coordinates manually placed by {overridden.get('by')}")
    elif overridden and overridden.get("type") == "candidate":
        parts.append(f"alternative location chosen by {overridden.get('by')}")
    if "ai_repaired" in flags:
        parts.append("input repaired by AI")
    return "; ".join(p for p in parts if p)


@router.get("/export")
async def export(job_id: uuid.UUID, scope: Scope = "confirmed", filter: str | None = Query(default=None),
                 auth: AuthContext = Depends(current_auth), db: AsyncSession = Depends(get_session)):  # fmt: skip
    job = await job_for(db, auth, job_id)
    row_filter = _scope_filter(scope, filter)
    where = row_filter.where(job.id)
    count = await db.scalar(select(func.count()).where(where)) or 0
    await audit(db, auth.user, "export", job_id=job.id, filter={"scope": scope, **row_filter.model_dump(exclude_defaults=True)},
                count=count)  # fmt: skip
    await db.commit()
    columns = job.file_meta.get("columns", [])
    width = len(columns)

    async def body():
        yield BOM
        for line in csv_lines([header(columns)]):
            yield line
        async with get_sessionmaker()() as s:
            q = (
                select(Row, AddressKey.corrections, User.username)
                .outerjoin(AddressKey, AddressKey.id == Row.address_key_id)
                .outerjoin(User, User.id == Row.reviewed_by)
                .where(where)
                .order_by(Row.row_index)
                .execution_options(yield_per=BATCH)
            )
            result = await s.stream(q)
            async for partition in result.partitions(BATCH):
                out = []
                for row, corrections, reviewer in partition:
                    out.append(export_row(row.raw, width, {
                        "lat": row.lat, "lon": row.lon, "confidence": row.score if row.score is not None else "",
                        "confidence_band": row.band or "", "precision_level": row.precision or "",
                        "geocode_source": row.source or "", "matched_street": row.m_street or "",
                        "matched_city": row.m_city or "", "matched_postcode": row.m_postcode or "",
                        "matched_country": row.m_country or "",
                        "corrections": _corrections(corrections, row.override if row.overridden else None, row.flags),
                        "status": row.status, "reviewed_by": reviewer or "",
                        "reviewed_at": row.reviewed_at.isoformat(timespec="seconds") if row.reviewed_at else "",
                    }))  # fmt: skip
                yield "".join(csv_lines(out))

    name = filename(job.name, scope, datetime.now(UTC))
    return StreamingResponse(
        (chunk.encode("utf-8") async for chunk in body()),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"', "X-Row-Count": str(count)},
    )
