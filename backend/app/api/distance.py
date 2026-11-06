"""Interactive distance calculator endpoint. Nothing here touches PostgreSQL (Google retention rules)."""

import logging
import math

import httpx
from fastapi import APIRouter, Depends, HTTPException

from app.auth.deps import AuthContext, current_auth
from app.config import Settings, get_settings
from app.distance.google import GoogleGeocoder, GoogleRoutes
from app.distance.models import DistanceRequest, DistanceResponse
from app.distance.service import AddressLookupUnavailable, DistanceService, LocationError
from app.jobs.ratelimit import RateLimiter
from app.redis import get_redis

log = logging.getLogger(__name__)
router = APIRouter(tags=["distance"])

_http: httpx.AsyncClient | None = None


def _client(settings: Settings) -> httpx.AsyncClient:
    global _http
    if _http is None:
        _http = httpx.AsyncClient(timeout=settings.google_timeout_s)
    return _http


def get_distance_service() -> DistanceService:
    settings = get_settings()
    redis = get_redis()
    key, lang = settings.google_maps_api_key, settings.google_language
    geocoder = GoogleGeocoder(key, _client(settings), lang) if key else None
    routes = GoogleRoutes(key, _client(settings), lang) if key and settings.google_routes_enabled else None
    return DistanceService(
        geocoder, routes, redis, RateLimiter(redis, "google", settings.google_rps),
        cache_ttl_s=settings.google_cache_ttl_hours * 3600,
    )  # fmt: skip


@router.post("/distance", response_model=DistanceResponse)
async def calculate_distance(
    req: DistanceRequest,
    auth: AuthContext = Depends(current_auth),
    service: DistanceService = Depends(get_distance_service),
) -> DistanceResponse:
    settings = get_settings()
    limiter = RateLimiter.per_window(get_redis(), f"distance:{auth.user.id}", settings.distance_per_user_per_min, 60)
    retry_after = await limiter.try_acquire()
    if retry_after is not None:
        raise HTTPException(
            429,
            {"code": "rate_limited", "message": "Too many distance calculations; please wait a moment."},
            headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
        )
    try:
        return await service.calculate(req)
    except AddressLookupUnavailable as e:
        if e.__cause__ is not None:
            log.warning("distance: address lookup failed: %s", e.__cause__)
        code = "address_lookup_unavailable" if e.configured else "address_lookup_not_configured"
        raise HTTPException(503, {"code": code, "message": str(e)}) from None
    except LocationError as e:
        raise HTTPException(
            422,
            {"code": e.code, "side": e.side, "message": str(e),
             "resolved": {k: v.model_dump() for k, v in e.resolved.items()}},
        ) from None  # fmt: skip
