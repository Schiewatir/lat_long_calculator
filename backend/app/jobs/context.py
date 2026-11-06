"""Services a pipeline run needs, built once per worker (or per request for single-row re-runs)."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.client import AIClient, build_ai_client
from app.config import Settings, get_settings
from app.db import get_sessionmaker
from app.gazetteer.store import DbGazetteer
from app.geocode.base import GeocoderProvider
from app.geocode.providers import build_provider
from app.jobs.ratelimit import RateLimiter
from app.models import ScoringConfig
from app.redis import get_redis
from app.scoring.engine import DEFAULT_CONFIG, merged_config


@dataclass
class PipelineContext:
    settings: Settings
    sessionmaker: async_sessionmaker[AsyncSession]
    gazetteer: DbGazetteer
    provider: GeocoderProvider
    ai: AIClient | None
    geo_limiter: RateLimiter
    ai_limiter: RateLimiter
    retry_base_delay: float = 0.5


def build_context(**overrides) -> PipelineContext:
    settings = get_settings()
    sm = get_sessionmaker()
    redis = get_redis()
    ctx = PipelineContext(
        settings=settings,
        sessionmaker=sm,
        gazetteer=DbGazetteer(sm),
        provider=build_provider(settings.geocoder_provider, settings.geocoder_api_key),
        ai=build_ai_client(settings),
        geo_limiter=RateLimiter(redis, "geocoder", settings.geocoder_rps),
        ai_limiter=RateLimiter(redis, "openai", settings.openai_rpm / 60),
    )
    for k, v in overrides.items():
        setattr(ctx, k, v)
    return ctx


async def active_scoring_config(session: AsyncSession) -> tuple[int, dict]:
    """(version, merged config); seeds version 1 from the defaults on first use."""
    row = await session.scalar(select(ScoringConfig).where(ScoringConfig.active.is_(True)))
    if row is None:
        row = await session.get(ScoringConfig, 1)
        if row is None:
            row = ScoringConfig(version=1, config=DEFAULT_CONFIG, active=True)
            session.add(row)
            await session.commit()
    return row.version, merged_config(row.config)


async def scoring_config_version(session: AsyncSession, version: int | None) -> dict:
    if version is None:
        return (await active_scoring_config(session))[1]
    row = await session.get(ScoringConfig, version)
    return merged_config(row.config if row else None)
