"""OpenAI Batch API path, SSE progress stream, file retention, calibration."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import update

from app.config import get_settings
from app.models import Job
from tests.conftest import login, make_user
from tests.fakes import FakeAI, echo_repair
from tests.test_flow import CSV, ai_enabled, all_rows, make_ctx, process, start, upload  # noqa: F401


async def test_large_job_uses_batch_api(client, db, ai_enabled, monkeypatch):  # noqa: F811
    monkeypatch.setattr(get_settings(), "ai_batch_threshold", 0)
    await make_user(db, "alice")
    await login(client, "alice")
    up = await upload(client)
    job_id = up["job"]["id"]
    await start(client, job_id, up["preview"]["mapping"], ai_repair=True)
    ai = FakeAI(echo_repair({"Frnakfurt a.M.": {"city": "Frankfurt am Main", "postcode": "60311", "country": "DE"}}))
    ai.batch_status = "in_progress"
    ctx = make_ctx(ai=ai)
    assert await process(job_id, ctx) == "ai_repair"
    job = (await client.get(f"/api/jobs/{job_id}")).json()
    assert job["message"].startswith("waiting for AI batch") and len(ai.batches) == 1
    # Rows that need no AI are already scored and reviewable while the batch runs.
    rows, _ = await all_rows(client, job_id)
    assert rows["Paris|75001"]["score"] == 100 and rows["Frnakfurt a.M.|6031"]["score"] is None
    assert await process(job_id, ctx) == "ai_repair"  # still running: nothing changes
    ai.batch_status = "completed"
    assert await process(job_id, ctx) == "ready"
    rows, _ = await all_rows(client, job_id)
    assert "ai_repaired" in rows["Frnakfurt a.M.|6031"]["flags"]
    assert len(ai.batches) == 1


async def test_sse_stream_for_finished_job(client, db):
    await make_user(db, "alice")
    await login(client, "alice")
    up = await upload(client)
    job_id = up["job"]["id"]
    await start(client, job_id, up["preview"]["mapping"])
    assert await process(job_id, make_ctx()) == "ready"
    async with client.stream("GET", f"/api/jobs/{job_id}/events") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        body = (await r.aread()).decode()
    assert body.startswith("data: ") and '"status": "ready"' in body and '"bands"' in body


async def test_cleanup_removes_old_source_files(client, db):
    from app.jobs.context import build_context
    from app.worker import cleanup_files

    await make_user(db, "alice")
    await login(client, "alice")
    up = await upload(client)
    job_id = up["job"]["id"]
    await start(client, job_id, up["preview"]["mapping"])
    assert await process(job_id, make_ctx()) == "ready"
    job = await db.get(Job, job_id)
    path = Path(job.file_path)
    assert path.exists()
    await db.execute(update(Job).values(created_at=datetime.now(UTC) - timedelta(days=31)))
    await db.commit()
    assert await cleanup_files({"pipeline": build_context()}) == 1
    assert not path.exists()
    rows, _ = await all_rows(client, job_id)
    assert len(rows) == 8  # rows stay until the job is deleted


async def test_calibration_report(db):
    from app.jobs.context import active_scoring_config
    from app.scoring.calibrate import calibrate_rows, format_report

    ctx = make_ctx(provider=__import__("app.geocode.providers", fromlist=["NullProvider"]).NullProvider())
    await ctx.gazetteer.refresh_version()
    _, config = await active_scoring_config(db)
    rows = [
        {"city": "Paris", "postcode": "75001", "country": "FR", "true_lat": "48.86", "true_lon": "2.34"},
        {"city": "Frankfurt", "country": "DE", "true_lat": "52.34", "true_lon": "14.55"},
    ]
    stats = await calibrate_rows(rows, ctx, config)
    assert stats["High"]["rows"] == 1 and stats["High"]["within_5km"] == 1
    assert stats["Low"]["rows"] == 1 and stats["Low"]["within_25km"] == 0  # ambiguous Frankfurt: wrong one picked
    assert "High" in format_report(stats)


async def test_gateway_without_batch_api_uses_concurrent_requests(client, db, ai_enabled, monkeypatch):  # noqa: F811
    monkeypatch.setattr(get_settings(), "ai_batch_threshold", 0)
    monkeypatch.setattr(get_settings(), "openai_batch_enabled", False)
    await make_user(db, "alice")
    await login(client, "alice")
    up = await upload(client)
    job_id = up["job"]["id"]
    await start(client, job_id, up["preview"]["mapping"], ai_repair=True)
    ai = FakeAI(echo_repair({"Frnakfurt a.M.": {"city": "Frankfurt am Main", "postcode": "60311", "country": "DE"}}))
    assert await process(job_id, make_ctx(ai=ai)) == "ready"
    assert ai.batches == {} and ai.requests
    rows, _ = await all_rows(client, job_id)
    assert "ai_repaired" in rows["Frnakfurt a.M.|6031"]["flags"]
