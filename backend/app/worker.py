"""arq worker: runs job pipelines, polls OpenAI batches, recovers stalled jobs, deletes old files."""

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from arq import cron
from arq.connections import ArqRedis, RedisSettings
from sqlalchemy import select

from app.config import get_settings
from app.jobs.context import build_context
from app.jobs.pipeline import run_job
from app.jobs.progress import ACTIVE
from app.models import Job

log = logging.getLogger(__name__)
LOCK_TTL = 120  # seconds; refreshed while a job runs


def _lock_key(job_id: str) -> str:
    return f"job_lock:{job_id}"


async def enqueue_job(redis: ArqRedis, job_id: uuid.UUID | str) -> None:
    await redis.enqueue_job("process_job", str(job_id), _job_id=f"process:{job_id}:{uuid.uuid4().hex[:8]}")


async def process_job(ctx: dict, job_id: str) -> str:
    redis: ArqRedis = ctx["redis"]
    token = uuid.uuid4().hex
    if not await redis.set(_lock_key(job_id), token, nx=True, ex=LOCK_TTL):
        return "already running"

    async def heartbeat() -> None:
        while True:
            await asyncio.sleep(LOCK_TTL / 3)
            await redis.expire(_lock_key(job_id), LOCK_TTL)

    hb = asyncio.create_task(heartbeat())
    try:
        return await run_job(uuid.UUID(job_id), ctx["pipeline"])
    finally:
        hb.cancel()
        if await redis.get(_lock_key(job_id)) in (token, token.encode()):
            await redis.delete(_lock_key(job_id))


async def recover_stalled_jobs(ctx: dict) -> int:
    """Re-enqueue active jobs that no worker holds a lock for (worker crash/restart)."""
    redis: ArqRedis = ctx["redis"]
    pipeline = ctx["pipeline"]
    n = 0
    async with pipeline.sessionmaker() as s:
        jobs = (await s.scalars(select(Job).where(Job.status.in_(ACTIVE)))).all()
    for job in jobs:
        if job.status == "ai_repair" and job.ai_batch_id:
            continue  # handled by poll_ai_batches
        if not await redis.exists(_lock_key(str(job.id))):
            await enqueue_job(redis, job.id)
            n += 1
    if n:
        log.info("re-enqueued %d stalled job(s)", n)
    return n


async def poll_ai_batches(ctx: dict) -> None:
    pipeline = ctx["pipeline"]
    if pipeline.ai is None:
        return
    async with pipeline.sessionmaker() as s:
        jobs = (await s.scalars(select(Job).where(Job.ai_batch_id.is_not(None), Job.status == "ai_repair"))).all()
    for job in jobs:
        status, _ = await pipeline.ai.batch_results(job.ai_batch_id)
        if status not in ("validating", "in_progress", "finalizing", "cancelling"):
            await enqueue_job(ctx["redis"], job.id)


async def cleanup_files(ctx: dict) -> int:
    """Delete uploaded source files after the retention period; rows stay until the job is deleted."""
    settings = get_settings()
    cutoff = datetime.now(UTC) - timedelta(days=settings.file_retention_days)
    pipeline = ctx["pipeline"]
    n = 0
    async with pipeline.sessionmaker() as s:
        jobs = (await s.scalars(select(Job).where(Job.created_at < cutoff, Job.file_deleted_at.is_(None),
                                                    Job.status.not_in(ACTIVE)))).all()  # fmt: skip
        for job in jobs:
            if job.file_path:
                Path(job.file_path).unlink(missing_ok=True)
            job.file_deleted_at = datetime.now(UTC)
            n += 1
        await s.commit()
    return n


async def startup(ctx: dict) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ctx["pipeline"] = build_context()
    await recover_stalled_jobs(ctx)


class WorkerSettings:
    functions = [process_job]
    cron_jobs = [
        cron(recover_stalled_jobs, second=0),
        cron(poll_ai_batches, second=30),
        cron(cleanup_files, hour=3, minute=15, second=0),
    ]
    on_startup = startup
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = 4
    job_timeout = 24 * 3600
    keep_result = 3600
    max_tries = 1
