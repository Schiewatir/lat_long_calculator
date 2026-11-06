"""Gazetteer lookups used by normalization and offline geocoding.

`Gazetteer` is implemented by `DbGazetteer` (PostgreSQL, see store.py) and `MemoryGazetteer`
(fixtures/tests). City matching inside one country runs on an in-memory `CityIndex`."""

import math
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Protocol

from rapidfuzz import fuzz, process

from app.normalize.text import fold, fold_variants


@dataclass(frozen=True)
class PostalRec:
    country: str
    postcode: str
    place_name: str
    admin1: str | None
    lat: float
    lon: float


@dataclass(frozen=True)
class NameRec:
    country: str
    folded: str
    name: str
    geonameid: int | None
    kind: str  # place | alt | base | postal
    lat: float
    lon: float
    population: int = 0


@dataclass
class Place:
    """A cluster of name records that refer to the same physical place."""

    name: str
    lat: float
    lon: float
    population: int
    country: str
    names: set[str] = field(default_factory=set)  # folded names known for this place
    kinds: set[str] = field(default_factory=set)
    geonameid: int | None = None


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def centroid(points: Iterable[tuple[float, float]]) -> tuple[float, float] | None:
    pts = list(points)
    if not pts:
        return None
    return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)


PLACE_MERGE_KM = 10.0


class CityIndex:
    """All known names of places in one country, for exact and fuzzy matching."""

    def __init__(self, country: str, names: Iterable[NameRec], postal: Iterable[PostalRec] = ()):
        self.country = country
        self.by_folded: dict[str, list[NameRec]] = defaultdict(list)
        self.names_of_place: dict[int, set[str]] = defaultdict(set)
        for rec in names:
            self.by_folded[rec.folded].append(rec)
            if rec.geonameid is not None:
                self.names_of_place[rec.geonameid].add(rec.folded)
        self.choices = list(self.by_folded.keys())
        # 0.1-degree grid of postal place names, used to display local names ("München" not "Munich")
        self._postal_grid: dict[tuple[int, int], list[PostalRec]] = defaultdict(list)
        for p in postal:
            self._postal_grid[(round(p.lat * 10), round(p.lon * 10))].append(p)

    def exact(self, variants: Iterable[str]) -> list[NameRec]:
        out: list[NameRec] = []
        for v in variants:
            out.extend(self.by_folded.get(v, ()))
        return out

    def fuzzy(self, query: str, cutoff: float, limit: int = 8) -> list[tuple[str, float]]:
        if not self.choices:
            return []
        hits = process.extract(query, self.choices, scorer=fuzz.ratio, score_cutoff=cutoff, limit=limit)
        return [(h[0], float(h[1])) for h in hits]

    def local_name(self, place: Place) -> str:
        """Prefer a nearby postal place name that is one of this place's known names."""
        gy, gx = round(place.lat * 10), round(place.lon * 10)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                for p in self._postal_grid.get((gy + dy, gx + dx), ()):
                    if fold_variants(p.place_name) & place.names and (
                        haversine_km(p.lat, p.lon, place.lat, place.lon) <= PLACE_MERGE_KM
                    ):
                        return p.place_name
        return place.name

    def cluster(self, recs: Iterable[NameRec]) -> list[Place]:
        """Group records of the same place (same geonameid, or within PLACE_MERGE_KM)."""
        places: list[Place] = []
        for rec in sorted(recs, key=lambda r: (r.kind == "postal", -r.population)):
            target = None
            for pl in places:
                same_id = rec.geonameid is not None and rec.geonameid == pl.geonameid
                if same_id or haversine_km(rec.lat, rec.lon, pl.lat, pl.lon) <= PLACE_MERGE_KM:
                    target = pl
                    break
            if target is None:
                target = Place(rec.name, rec.lat, rec.lon, rec.population, rec.country, geonameid=rec.geonameid)
                places.append(target)
            target.names.add(rec.folded)
            target.names |= fold_variants(rec.name)
            target.kinds.add(rec.kind)
            target.population = max(target.population, rec.population)
            if rec.geonameid is not None:
                target.names |= self.names_of_place.get(rec.geonameid, set())
        for pl in places:
            pl.name = self.local_name(pl) if pl.geonameid is not None else pl.name
        return places


class Gazetteer(Protocol):
    async def has_postal_data(self, country: str) -> bool: ...

    async def postal(self, country: str, keys: list[str]) -> tuple[str | None, list[PostalRec]]:
        """Records for the first key that exists in the country, and that key."""
        ...

    async def postal_any(self, key: str) -> dict[str, list[PostalRec]]:
        """Records per country for a compact postcode key, across all countries."""
        ...

    async def names_any(self, folded: str) -> list[NameRec]:
        """Exact folded-name matches across all countries."""
        ...

    async def city_index(self, country: str) -> CityIndex: ...

    async def prefetch(self, postal_keys: set[str], folded_names: set[str]) -> None: ...


def postal_key(postcode: str) -> str:
    from app.normalize.postcode import compact

    return compact(postcode)


class MemoryGazetteer:
    """In-memory gazetteer built from record lists (tests, fixtures, calibration)."""

    def __init__(self, postal: list[PostalRec], names: list[NameRec]):
        self._postal: dict[tuple[str, str], list[PostalRec]] = defaultdict(list)
        self._postal_by_key: dict[str, dict[str, list[PostalRec]]] = defaultdict(lambda: defaultdict(list))
        self._postal_by_country: dict[str, list[PostalRec]] = defaultdict(list)
        for p in postal:
            k = postal_key(p.postcode)
            self._postal[(p.country, k)].append(p)
            self._postal_by_key[k][p.country].append(p)
            self._postal_by_country[p.country].append(p)
        self._names: dict[str, list[NameRec]] = defaultdict(list)
        self._names_by_country: dict[str, list[NameRec]] = defaultdict(list)
        for n in names:
            self._names[n.folded].append(n)
            self._names_by_country[n.country].append(n)
        self._indexes: dict[str, CityIndex] = {}

    async def has_postal_data(self, country: str) -> bool:
        return country in self._postal_by_country

    async def postal(self, country: str, keys: list[str]) -> tuple[str | None, list[PostalRec]]:
        for k in keys:
            if recs := self._postal.get((country, k)):
                return k, recs
        return None, []

    async def postal_any(self, key: str) -> dict[str, list[PostalRec]]:
        return {c: list(v) for c, v in self._postal_by_key.get(key, {}).items()}

    async def names_any(self, folded: str) -> list[NameRec]:
        return list(self._names.get(folded, ()))

    async def city_index(self, country: str) -> CityIndex:
        if country not in self._indexes:
            self._indexes[country] = CityIndex(
                country, self._names_by_country.get(country, ()), self._postal_by_country.get(country, ())
            )
        return self._indexes[country]

    async def prefetch(self, postal_keys: set[str], folded_names: set[str]) -> None:
        return None


def name_records_for_place(
    country: str, geonameid: int | None, name: str, alt_names: Iterable[str], lat: float, lon: float,
    population: int, kind: str = "place",
) -> list[NameRec]:
    """All index records for a place: primary name, alternates, umlaut variants and base names."""
    from app.normalize.text import base_name

    out: dict[str, NameRec] = {}  # first kind wins: place > alt > base

    def add(folded: str, k: str) -> None:
        if folded and folded not in out:
            out[folded] = NameRec(country, folded, name, geonameid, k, lat, lon, population)

    for v in fold_variants(name):
        add(v, kind)
    for alt in alt_names:
        for v in fold_variants(alt):
            add(v, "alt")
    for n in [name, *alt_names]:
        if b := base_name(n):
            for v in fold_variants(b):
                add(v, "base")
    return list(out.values())


def postal_name_records(recs: Iterable[PostalRec]) -> list[NameRec]:
    """Index records for postal place names (deduplicated per name and ~1 km cell)."""
    out: dict[tuple[str, str, int, int], NameRec] = {}
    for p in recs:
        for v in fold_variants(p.place_name):
            key = (p.country, v, round(p.lat * 100), round(p.lon * 100))
            out.setdefault(key, NameRec(p.country, v, p.place_name, None, "postal", p.lat, p.lon, 0))
        if b := _postal_base(p.place_name):
            for v in fold_variants(b):
                key = (p.country, v, round(p.lat * 100), round(p.lon * 100))
                out.setdefault(key, NameRec(p.country, v, p.place_name, None, "base", p.lat, p.lon, 0))
    return list(out.values())


def _postal_base(place_name: str) -> str | None:
    from app.normalize.text import base_name

    return base_name(place_name)


__all__ = [
    "CityIndex",
    "Gazetteer",
    "MemoryGazetteer",
    "NameRec",
    "Place",
    "PostalRec",
    "centroid",
    "fold",
    "haversine_km",
    "name_records_for_place",
    "postal_key",
    "postal_name_records",
]
