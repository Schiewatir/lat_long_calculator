"""Choose between offline gazetteer lookups and the external provider; validate and rank candidates."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.gazetteer.base import haversine_km
from app.geocode.base import PRECISION_RANK, GeoCandidate, GeocoderProvider, ProviderError, ProviderQuery
from app.geocode.providers import with_retries
from app.models import GeocodeCache
from app.normalize.pipeline import NormResult

MAX_CANDIDATES = 5


class Limiter(Protocol):
    async def acquire(self) -> None: ...


class GeocodeCacheStore(Protocol):
    async def get(self, provider: str, key: str) -> list[GeoCandidate] | None: ...

    async def put(self, provider: str, key: str, query: ProviderQuery, cands: list[GeoCandidate]) -> None: ...


class DbGeocodeCache:
    """Provider responses shared across all users and jobs."""

    def __init__(self, session: AsyncSession, ttl_days: int):
        self.session = session
        self.ttl = timedelta(days=ttl_days)

    async def get(self, provider: str, key: str) -> list[GeoCandidate] | None:
        row = await self.session.scalar(
            select(GeocodeCache).where(GeocodeCache.provider == provider, GeocodeCache.query_hash == key)
        )
        if row is None or row.created_at < datetime.now(UTC) - self.ttl:
            return None
        return [GeoCandidate.from_dict(d) for d in row.response]

    async def put(self, provider: str, key: str, query: ProviderQuery, cands: list[GeoCandidate]) -> None:
        stmt = insert(GeocodeCache).values(
            provider=provider, query_hash=key, query=query.__dict__, response=[c.to_dict() for c in cands]
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["provider", "query_hash"],
            set_={"response": stmt.excluded.response, "created_at": datetime.now(UTC)},
        )
        await self.session.execute(stmt)


class MemoryGeocodeCache:
    def __init__(self) -> None:
        self.data: dict[tuple[str, str], list[GeoCandidate]] = {}

    async def get(self, provider: str, key: str) -> list[GeoCandidate] | None:
        return self.data.get((provider, key))

    async def put(self, provider: str, key: str, query: ProviderQuery, cands: list[GeoCandidate]) -> None:
        self.data[(provider, key)] = cands


@dataclass
class GeocodeOutcome:
    candidates: list[GeoCandidate] = field(default_factory=list)
    provider_calls: int = 0
    cache_hits: int = 0
    error: str | None = None  # geocode_error


def geocode_offline(n: NormResult) -> list[GeoCandidate]:
    out: list[GeoCandidate] = []
    if n.pc_lat is not None and n.pc_lon is not None:
        flags = ["postcode_partial"] if n.postcode_partial else []
        # When postcode and city agree, show the matched city; on a conflict show the postcode's own place.
        place = n.city if n.pc_city_linked is not False else (n.pc_place or n.city)
        out.append(GeoCandidate(n.pc_lat, n.pc_lon, "geonames_postcode", "postcode",
                                f"{n.postcode} {place or ''}".strip(),
                                {"postcode": n.postcode, "city": place, "country": n.country}, None, flags))  # fmt: skip
    if n.city_match == "ambiguous" and n.city_candidates:
        for c in n.city_candidates:
            out.append(GeoCandidate(c["lat"], c["lon"], "geonames_city", "city", c["name"],
                                    {"city": c["name"], "country": n.country}, None, ["ambiguous"]))  # fmt: skip
    elif n.city_lat is not None and n.city_lon is not None:
        out.append(GeoCandidate(n.city_lat, n.city_lon, "geonames_city", "city", n.city or "",
                                {"city": n.city, "country": n.country}))  # fmt: skip
    return out


def _validate(cands: list[GeoCandidate], n: NormResult, mismatch_km: float) -> None:
    if n.pc_lat is None or n.pc_lon is None:
        return
    for c in cands:
        if c.source.startswith("provider:") and haversine_km(c.lat, c.lon, n.pc_lat, n.pc_lon) > mismatch_km:
            c.flags.append("postcode_mismatch")


def _rank(cands: list[GeoCandidate]) -> list[GeoCandidate]:
    return sorted(
        cands,
        key=lambda c: ("postcode_mismatch" in c.flags, PRECISION_RANK.get(c.precision, 9), -(c.provider_confidence or 0)),
    )


async def geocode_normalized(
    n: NormResult,
    provider: GeocoderProvider,
    cache: GeocodeCacheStore,
    limiter: Limiter,
    *,
    mismatch_km: float = 25.0,
    retry_base_delay: float = 0.5,
) -> GeocodeOutcome:
    """Offline first for rows without a street; provider for street rows and offline misses.
    Raises ProviderError(quota=True) so the job can pause."""
    outcome = GeocodeOutcome()
    offline = geocode_offline(n)
    has_provider = provider.name != "none"
    meaningful = any(ch.isalnum() for ch in f"{n.street}{n.city or ''}{n.postcode or ''}")
    want_provider = has_provider and meaningful and (bool(n.street) or (not offline and bool(n.city or n.postcode)))
    if not want_provider:
        outcome.candidates = offline[:MAX_CANDIDATES]
        return outcome

    query = ProviderQuery(
        street=n.street, house_number=n.house_number, postcode=n.postcode or "", city=n.city or "", country=n.country
    )
    key = query.cache_key()
    cached = await cache.get(provider.name, key)
    if cached is not None:
        outcome.cache_hits = 1
        provider_cands = cached
    else:
        try:
            async def call() -> list[GeoCandidate]:
                await limiter.acquire()
                return await provider.geocode(query)

            provider_cands = await with_retries(call, base_delay=retry_base_delay)
            outcome.provider_calls = 1
            await cache.put(provider.name, key, query, provider_cands)
        except ProviderError as e:
            if e.quota:
                raise
            outcome.error = "geocode_error"
            outcome.candidates = offline[:MAX_CANDIDATES]
            return outcome

    provider_cands = [GeoCandidate.from_dict(c.to_dict()) for c in provider_cands]  # don't mutate cached objects
    _validate(provider_cands, n, mismatch_km)
    ranked = _rank(provider_cands)
    # A provider result coarser than what we know offline is less useful than the offline result.
    best_offline = PRECISION_RANK.get(offline[0].precision, 9) if offline else 9
    useful = [c for c in ranked if PRECISION_RANK.get(c.precision, 9) < best_offline and "postcode_mismatch" not in c.flags]
    others = [c for c in ranked if c not in useful]
    outcome.candidates = (useful + offline + others)[:MAX_CANDIDATES]
    return outcome
