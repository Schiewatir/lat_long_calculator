"""PostgreSQL-backed gazetteer with per-process caches.

Cross-country lookups for a whole chunk are batched through `prefetch()`; per-country city
indexes are built lazily and kept in a small LRU (they hold every known name of a country)."""

import asyncio
from collections import OrderedDict, defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.gazetteer.base import CityIndex, NameRec, PostalRec
from app.models import GazetteerMeta, GazetteerName, GeonamesPostal

INDEX_LRU_SIZE = 12
_PREFETCH_MAX = 200_000


def _postal(row: GeonamesPostal) -> PostalRec:
    return PostalRec(row.country, row.postcode, row.place_name, row.admin1, row.lat, row.lon)


def _name(row: GazetteerName) -> NameRec:
    return NameRec(row.country, row.name_folded, row.name, row.geonameid, row.kind, row.lat, row.lon, row.population)


class _ProcessCache:
    """Survives across jobs in one worker; reset when a new gazetteer import is detected."""

    def __init__(self) -> None:
        self.version: str | None = None
        self.indexes: OrderedDict[str, CityIndex] = OrderedDict()
        self.postal_countries: set[str] | None = None
        self.lock = asyncio.Lock()

    def reset(self, version: str | None) -> None:
        self.version = version
        self.indexes.clear()
        self.postal_countries = None


_cache = _ProcessCache()


class DbGazetteer:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]):
        self.sessionmaker = sessionmaker
        self._postal_by_key: dict[str, dict[str, list[PostalRec]]] = {}
        self._names_by_folded: dict[str, list[NameRec]] = {}

    async def refresh_version(self) -> None:
        async with self.sessionmaker() as s:
            version = await s.scalar(select(GazetteerMeta.value).where(GazetteerMeta.key == "imported_at"))
        if version != _cache.version:
            _cache.reset(version)

    async def has_postal_data(self, country: str) -> bool:
        if _cache.postal_countries is None:
            async with self.sessionmaker() as s:
                value = await s.scalar(select(GazetteerMeta.value).where(GazetteerMeta.key == "postal_country_list"))
            _cache.postal_countries = set(value.split(",")) if value else set()
        return country in _cache.postal_countries

    async def prefetch(self, postal_keys: set[str], folded_names: set[str]) -> None:
        if len(self._postal_by_key) > _PREFETCH_MAX or len(self._names_by_folded) > _PREFETCH_MAX:
            self._postal_by_key.clear()
            self._names_by_folded.clear()
        need_pc = [k for k in postal_keys if k and k not in self._postal_by_key]
        need_names = [n for n in folded_names if n and n not in self._names_by_folded]
        async with self.sessionmaker() as s:
            for i in range(0, len(need_pc), 5000):
                part = need_pc[i : i + 5000]
                found: dict[str, dict[str, list[PostalRec]]] = {k: defaultdict(list) for k in part}
                for row in await s.scalars(select(GeonamesPostal).where(GeonamesPostal.pc_key.in_(part))):
                    found[row.pc_key][row.country].append(_postal(row))
                self._postal_by_key.update({k: dict(v) for k, v in found.items()})
            for i in range(0, len(need_names), 5000):
                part = need_names[i : i + 5000]
                got: dict[str, list[NameRec]] = {k: [] for k in part}
                for row in await s.scalars(select(GazetteerName).where(GazetteerName.name_folded.in_(part))):
                    got[row.name_folded].append(_name(row))
                self._names_by_folded.update(got)

    async def postal_any(self, key: str) -> dict[str, list[PostalRec]]:
        if key not in self._postal_by_key:
            await self.prefetch({key}, set())
        return self._postal_by_key.get(key, {})

    async def postal(self, country: str, keys: list[str]) -> tuple[str | None, list[PostalRec]]:
        await self.prefetch(set(keys), set())
        for k in keys:
            if recs := self._postal_by_key.get(k, {}).get(country):
                return k, recs
        return None, []

    async def names_any(self, folded: str) -> list[NameRec]:
        if folded not in self._names_by_folded:
            await self.prefetch(set(), {folded})
        return self._names_by_folded.get(folded, [])

    async def city_index(self, country: str) -> CityIndex:
        if country in _cache.indexes:
            _cache.indexes.move_to_end(country)
            return _cache.indexes[country]
        async with _cache.lock:
            if country not in _cache.indexes:
                async with self.sessionmaker() as s:
                    names = [_name(r) for r in await s.scalars(select(GazetteerName).where(GazetteerName.country == country))]
                    postal = [_postal(r) for r in await s.scalars(select(GeonamesPostal).where(GeonamesPostal.country == country))]
                _cache.indexes[country] = await asyncio.to_thread(CityIndex, country, names, postal)
                while len(_cache.indexes) > INDEX_LRU_SIZE:
                    _cache.indexes.popitem(last=False)
        return _cache.indexes[country]
