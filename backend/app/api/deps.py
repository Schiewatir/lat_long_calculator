import uuid

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import AuthContext
from app.config import get_settings
from app.models import Job

_pool: ArqRedis | None = None


async def get_queue() -> ArqRedis:
    global _pool
    if _pool is None:
        _pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    return _pool


async def job_for(db: AsyncSession, auth: AuthContext, job_id: uuid.UUID, *, for_update: bool = False) -> Job:
    """The job if the caller owns it or is an admin; 404 otherwise (existence is not revealed)."""
    job = await db.get(Job, job_id, with_for_update=for_update)
    if job is None or (job.owner_id != auth.user.id and not auth.is_admin):
        raise HTTPException(404, "Job not found")
    return job
