"""Resolve origin/destination and compute straight-line and road distance."""

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Protocol

from app.distance.google import GoogleGeocoder, GoogleRoutes, InvalidQuery, Place, Route
from app.distance.models import (
    AddressInput,
    Alternative,
    CoordsInput,
    Distance,
    DistanceRequest,
    DistanceResponse,
    ResolvedLocation,
    RoadDistance,
    RoadUnavailable,
)
from app.gazetteer.base import haversine_km
from app.geocode.base import ProviderError
from app.geocode.providers import with_retries

ATTRIBUTION_GOOGLE = "Geocoding and routing by Google"
RETRY_ATTEMPTS = 3  # a person is waiting; jobs use 5


class Limiter(Protocol):
    async def acquire(self) -> None: ...


class JsonCache(Protocol):
    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str, ex: int | None = None) -> object: ...


class AddressLookupUnavailable(Exception):
    """No Google key configured, or Google refused/failed (quota, denied key, persistent errors)."""

    def __init__(self, message: str, *, configured: bool = True):
        super().__init__(message)
        self.configured = configured


class LocationError(Exception):
    """One side could not be resolved (not found / invalid query); the other side may be resolved."""

    def __init__(self, side: str, code: str, message: str, resolved: dict[str, ResolvedLocation]):
        super().__init__(message)
        self.side = side
        self.code = code
        self.resolved = resolved


def geocode_cache_key(a: AddressInput, language: str) -> str:
    payload = json.dumps({**a.model_dump(exclude={"type"}), "lang": language}, sort_keys=True, ensure_ascii=False)
    return "gdist:geo:" + hashlib.sha256(payload.lower().encode()).hexdigest()


def route_cache_key(o: tuple[float, float], d: tuple[float, float]) -> str:
    return f"gdist:route:{o[0]:.6f},{o[1]:.6f}|{d[0]:.6f},{d[1]:.6f}"


class DistanceService:
    def __init__(
        self,
        geocoder: GoogleGeocoder | None,
        routes: GoogleRoutes | None,
        cache: JsonCache,
        limiter: Limiter,
        *,
        cache_ttl_s: int,
        retry_base_delay: float = 0.5,
    ):
        self.geocoder = geocoder
        self.routes = routes
        self.cache = cache
        self.limiter = limiter
        self.ttl = cache_ttl_s
        self.retry_base_delay = retry_base_delay

    async def _call[T](self, fn: Callable[[], Awaitable[T]]) -> T:
        async def limited() -> T:
            await self.limiter.acquire()
            return await fn()

        return await with_retries(limited, attempts=RETRY_ATTEMPTS, base_delay=self.retry_base_delay)

    async def _geocode(self, a: AddressInput) -> list[Place]:
        if self.geocoder is None:
            raise AddressLookupUnavailable("Address lookup not configured", configured=False)
        geocoder = self.geocoder
        key = geocode_cache_key(a, geocoder.language)
        if self.ttl and (hit := await self.cache.get(key)) is not None:
            return [Place(**p) for p in json.loads(hit)]
        places = await self._call(lambda: geocoder.geocode(a))
        if self.ttl:
            await self.cache.set(key, json.dumps([asdict(p) for p in places]), ex=self.ttl)
        return places

    async def _resolve(self, loc: AddressInput | CoordsInput) -> ResolvedLocation:
        if isinstance(loc, CoordsInput):
            return ResolvedLocation(
                label=loc.label or f"{loc.lat:.6f}, {loc.lon:.6f}", lat=loc.lat, lon=loc.lon,
                source="google" if loc.place_id else "input", precision=loc.precision or "manual",
                place_id=loc.place_id,
            )  # fmt: skip
        places = await self._geocode(loc)
        if not places:
            raise LookupError("not_found")
        top, *alts = places
        return ResolvedLocation(
            label=top.label, lat=top.lat, lon=top.lon, source="google", precision=top.precision,
            place_id=top.place_id, partial_match=top.partial_match,
            alternatives=[Alternative(**asdict(p)) for p in alts],
        )  # fmt: skip

    async def _route(self, o: tuple[float, float], d: tuple[float, float]) -> Route | None:
        assert self.routes is not None
        routes = self.routes
        key = route_cache_key(o, d)
        if self.ttl and (hit := await self.cache.get(key)) is not None:
            data = json.loads(hit)
            return Route(**data) if data else None
        route = await self._call(lambda: routes.compute(o, d))
        if self.ttl:
            await self.cache.set(key, json.dumps(asdict(route) if route else None), ex=self.ttl)
        return route

    async def calculate(self, req: DistanceRequest) -> DistanceResponse:
        sides = {"origin": req.origin, "destination": req.destination}
        results = await asyncio.gather(*(self._resolve(v) for v in sides.values()), return_exceptions=True)
        resolved = {s: r for s, r in zip(sides, results, strict=True) if isinstance(r, ResolvedLocation)}
        for r in results:
            if isinstance(r, AddressLookupUnavailable):
                raise r
            if isinstance(r, ProviderError):
                raise AddressLookupUnavailable("Address lookup temporarily unavailable") from r
        for side, r in zip(sides, results, strict=True):
            if isinstance(r, LookupError):
                raise LocationError(side, "location_not_found", f"The {side} could not be found.", resolved)
            if isinstance(r, InvalidQuery):
                raise LocationError(side, "invalid_location", f"The {side} address is not a valid query.", resolved)
            if isinstance(r, BaseException):
                raise r
        origin, destination = resolved["origin"], resolved["destination"]
        o, d = (origin.lat, origin.lon), (destination.lat, destination.lon)

        road: RoadDistance | None = None
        reason: RoadUnavailable | None = None
        if o == d:
            reason = "same_point"
        elif not req.road or self.routes is None:
            reason = "disabled"
        else:
            try:
                route = await self._route(o, d)
            except ProviderError:
                route, reason = None, "provider_error"
            if route is None and reason is None:
                reason = "no_route"
            elif route is not None:
                km = route.distance_m / 1000
                road = RoadDistance(**Distance.from_km(km).model_dump(), duration_s=route.duration_s,
                                    polyline=route.polyline)  # fmt: skip

        uses_google = road is not None or any(x.source == "google" for x in (origin, destination))
        return DistanceResponse(
            origin=origin, destination=destination,
            straight_line=Distance.from_km(0.0 if o == d else haversine_km(*o, *d)),
            road=road, road_distance_unavailable=reason,
            attribution=[ATTRIBUTION_GOOGLE] if uses_google else [],
        )  # fmt: skip
