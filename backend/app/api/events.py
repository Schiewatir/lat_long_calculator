"""Server-Sent Events: live job progress from Redis pub/sub, with a database poll as fallback."""

import asyncio
import json
import time
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import job_for
from app.auth.deps import AuthContext, current_auth
from app.db import get_session, get_sessionmaker
from app.jobs.progress import TERMINAL, band_counts, channel, job_snapshot
from app.models import Job
from app.redis import get_redis

router = APIRouter(prefix="/jobs/{job_id}", tags=["jobs"])
POLL_SECONDS = 2.0
KEEPALIVE_SECONDS = 15.0


async def _db_snapshot(job_id: uuid.UUID) -> dict | None:
    async with get_sessionmaker()() as s:
        job = await s.get(Job, job_id)
        if job is None:
            return None
        return job_snapshot(job, await band_counts(s, job.id))


def _event(data: dict) -> str:
    return f"data: {json.dumps(data, default=str)}\n\n"


@router.get("/events")
async def events(job_id: uuid.UUID, request: Request, auth: AuthContext = Depends(current_auth),
                 db: AsyncSession = Depends(get_session)):  # fmt: skip
    job = await job_for(db, auth, job_id)
    await db.close()

    async def stream():
        snap = await _db_snapshot(job.id)
        if snap is None:
            return
        yield _event(snap)
        if snap["status"] in TERMINAL | {"draft"}:
            return
        pubsub = get_redis().pubsub()
        await pubsub.subscribe(channel(job.id))
        last_db_poll = last_sent = time.monotonic()
        try:
            while not await request.is_disconnected():
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=POLL_SECONDS)
                now = time.monotonic()
                data = json.loads(msg["data"]) if msg and msg.get("type") == "message" else None
                if data is None and now - last_db_poll >= POLL_SECONDS:
                    # Fallback: messages may be missed (e.g. Redis restart); re-read state from the DB.
                    data = await _db_snapshot(job.id)
                    last_db_poll = now
                if data is not None:
                    yield _event(data)
                    last_sent = now
                    if data.get("status") in TERMINAL:
                        final = await _db_snapshot(job.id)
                        if final:
                            yield _event(final)
                        return
                elif now - last_sent >= KEEPALIVE_SECONDS:
                    yield ": keepalive\n\n"
                    last_sent = now
                await asyncio.sleep(0)
        finally:
            await pubsub.unsubscribe(channel(job.id))
            await pubsub.aclose()

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})  # fmt: skip
