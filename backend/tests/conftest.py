import os
import tempfile
from pathlib import Path

# Must be set before any app module reads settings.
os.environ.update(
    DATABASE_URL="postgresql+asyncpg://geo:geo@localhost:5432/geo_test",
    REDIS_URL="redis://localhost:6379/15",
    COOKIE_SECURE="false",
    OPENAI_API_KEY="",
    OPENAI_BASE_URL="",
    OPENAI_CHAT_DEPLOYMENT="",
    OPENAI_API_VERSION="",
    OPENAI_AUTH_STYLE="bearer",
    OPENAI_BATCH_ENABLED="true",
    GEOCODER_API_KEY="",
    GOOGLE_MAPS_API_KEY="",
    UPLOAD_DIR=tempfile.mkdtemp(prefix="gw-uploads-"),
)

import asyncpg  # noqa: E402
import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]
TEST_DB_TABLES = ["audit_log", "candidates", "rows", "address_keys", "jobs", "sessions", "users", "geocode_cache",
                  "scoring_config"]  # fmt: skip


async def _ensure_db() -> None:
    conn = await asyncpg.connect("postgresql://geo:geo@localhost:5432/geo")
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = 'geo_test'")
        if not exists:
            await conn.execute("CREATE DATABASE geo_test")
    finally:
        await conn.close()


@pytest.fixture(scope="session")
async def database():
    import subprocess

    await _ensure_db()
    subprocess.run([str(BACKEND / ".venv/bin/alembic"), "upgrade", "head"], cwd=BACKEND, check=True,
                   env={**os.environ}, capture_output=True)  # fmt: skip

    from app.db import get_sessionmaker, raw_connection
    from app.gazetteer.importer import import_geonames, write_geonames_files
    from tests.fixtures import PLACES, POSTAL

    with tempfile.TemporaryDirectory() as tmp:
        write_geonames_files(Path(tmp), POSTAL, PLACES)
        async with get_sessionmaker()() as s:
            await import_geonames(await raw_connection(s), Path(tmp))
            await s.commit()
    yield


@pytest.fixture
async def db(database):
    from app.db import get_sessionmaker
    from app.redis import get_redis

    async with get_sessionmaker()() as s:
        await s.execute(text(f"TRUNCATE {', '.join(TEST_DB_TABLES)} RESTART IDENTITY CASCADE"))
        await s.commit()
    await get_redis().flushdb()
    async with get_sessionmaker()() as s:
        yield s


@pytest.fixture
async def client(db, monkeypatch):
    """HTTP client against the app; job enqueueing is captured instead of sent to arq."""
    import httpx

    from app.main import app

    queued: list[str] = []

    async def fake_enqueue(_redis, job_id):
        queued.append(str(job_id))

    async def fake_queue():
        return None

    monkeypatch.setattr("app.api.jobs.enqueue_job", fake_enqueue)
    monkeypatch.setattr("app.api.jobs.get_queue", fake_queue)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        c.queued = queued  # type: ignore[attr-defined]
        yield c


async def make_user(db, username: str, password: str = "correct horse battery", role: str = "user", **kw):
    from app.auth.security import hash_password
    from app.models import User

    u = User(username=username, password_hash=hash_password(password), role=role, **kw)
    db.add(u)
    await db.commit()
    return u


async def login(client, username: str, password: str = "correct horse battery"):
    r = await client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    client.headers["X-CSRF-Token"] = client.cookies.get("gw_csrf")
    return r.json()
