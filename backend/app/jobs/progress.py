import json
import time
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Job, Row
from app.redis import get_redis

STAGES = ("ingesting", "normalizing", "ai_repair", "geocoding", "scoring")
ACTIVE = {"queued", *STAGES}
TERMINAL = {"ready", "failed", "cancelled"}


def channel(job_id: uuid.UUID | str) -> str:
    return f"job:{job_id}"


async def band_counts(session: AsyncSession, job_id: uuid.UUID) -> dict:
    rows = await session.execute(
        select(Row.band, Row.status, func.count()).where(Row.job_id == job_id).group_by(Row.band, Row.status)
    )
    bands: dict[str, int] = {}
    review: dict[str, int] = {"pending": 0, "confirmed": 0, "edited": 0, "rejected": 0}
    per_band: dict[str, dict[str, int]] = {}
    for band, status, n in rows:
        key = band or "unscored"
        bands[key] = bands.get(key, 0) + n
        review[status] = review.get(status, 0) + n
        per_band.setdefault(key, {}).setdefault(status, 0)
        per_band[key][status] += n
    return {"bands": bands, "review": review, "review_by_band": per_band}


def job_snapshot(job: Job, extra: dict | None = None) -> dict:
    return {
        "id": str(job.id),
        "status": job.status,
        "message": job.message,
        "counts": job.counts,
        "ts": time.time(),
        **(extra or {}),
    }


async def publish(job: Job, extra: dict | None = None) -> None:
    await get_redis().publish(channel(job.id), json.dumps(job_snapshot(job, extra), default=str))


class StageTimer:
    """ETA estimate for the current stage."""

    def __init__(self, total: int, done: int = 0):
        self.total, self.start_done, self.start = total, done, time.monotonic()

    def eta(self, done: int) -> float | None:
        progressed = done - self.start_done
        if progressed <= 0 or done >= self.total:
            return None
        rate = progressed / max(time.monotonic() - self.start, 1e-6)
        return round((self.total - done) / rate, 1)
